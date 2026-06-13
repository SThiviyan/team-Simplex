"""LLM semantic-filter layer for the company-registry verification pipeline.

Pipeline:  gather records → RapidFuzz gross filter → **LLM semantic filter**

The RapidFuzz layer (``company_matcher.py``) hands this module a short list of
candidate registry records that survived string-similarity filtering. This
module performs the deep semantic evaluation that string matchers cannot:

1. **Acronym expansion** — "BMW" ↔ "Bayerische Motoren Werke AG",
   "BASF" ↔ "Badische Anilin- und Soda-Fabrik".
2. **Legal-suffix normalisation** — German forms (GmbH, AG, e.V., Co. KG, …);
   "Sinpex" matching "Sinpex GmbH" should score high.
3. **Confidence calibration** — a single re-scored confidence on [0.0, 1.0].
4. **Recursive triggering** — if none of the candidates match but the query is a
   recognisable acronym/alias whose expansion is *absent* from the candidates,
   flag a recursive search and suggest the expanded name so the pipeline can
   loop back and re-query the database.

The call to Claude is made via the official Anthropic SDK using **forced tool
use** (function calling): the model is required to invoke a single
``submit_evaluation`` tool, so the response is always schema-shaped — no
conversational prose, no markdown fences, no JSON-from-text parsing.

The winning candidate is returned in the *same shape* as the input record
(see ``company_matcher.py``), with its ``confidence`` field overwritten by the
LLM's calibrated value.

Set ``mock=True`` (or run with PIPELINE_MOCK on / no ANTHROPIC_API_KEY) to skip
the API call entirely and deterministically pick the top fuzzy candidate.

Requirements:
    pip install anthropic
    export ANTHROPIC_API_KEY=...
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# Default to the most capable Claude model. Override per call if needed.
DEFAULT_MODEL = "claude-opus-4-8"

# Field names in the registry records (must match company_matcher.py).
NAME_FIELD = "name_normalized_register_name"
JURISDICTION_FIELD = "jurisdiction_confirmed"
CONFIDENCE_FIELD = "confidence"

# Possible decisions the model can return.
DECISION_MATCH = "match"
DECISION_NO_MATCH = "no_match"
DECISION_RECURSIVE = "recursive_search"


class SemanticFilterError(RuntimeError):
    """Raised when the semantic filter cannot produce a usable result."""


SYSTEM_PROMPT = """\
You are the semantic-verification layer of a company-registry matching pipeline.
You receive a user's search query (a company name plus a target jurisdiction) and
a shortlist of candidate registry records that a fuzzy string matcher already
considered plausible. Your job is to decide, using real-world company knowledge,
which candidate (if any) the user actually meant.

Apply reasoning that pure string matching cannot:

1. ACRONYM / ALIAS EXPANSION. Recognise that an abbreviation or common name
   refers to a full registered name even when the strings barely overlap:
     - "BMW"  -> "Bayerische Motoren Werke AG"
     - "BASF" -> "Badische Anilin- und Soda-Fabrik" / "BASF SE"
     - "VW"   -> "Volkswagen AG"
   A query that is a well-known acronym for a candidate's full name is a strong
   match even though token similarity is low.

2. LEGAL-SUFFIX NORMALISATION. Treat German (and other) legal-form suffixes as
   non-discriminating when deciding identity: GmbH, AG, SE, e.V., e.K.,
   GmbH & Co. KG, KG, OHG, mbH, gGmbH. A query of "Sinpex" matching a record
   "Sinpex GmbH" is a high-confidence match — the missing suffix is not evidence
   against a match. Conversely, a different *core* name is a mismatch regardless
   of a shared suffix.

3. JURISDICTION. The user supplied a target jurisdiction (e.g. "DE"). A candidate
   whose jurisdiction disagrees should be treated with strong suspicion and, all
   else equal, not selected.

4. CONFIDENCE CALIBRATION. Output a single confidence in [0.0, 1.0] reflecting
   how certain you are that the chosen candidate is the entity the user meant:
     - 0.90-1.00: essentially certain (exact core name, or unambiguous acronym).
     - 0.70-0.89: strong match with minor uncertainty.
     - 0.40-0.69: plausible but genuinely ambiguous.
     - 0.00-0.39: weak; likely not a real match.

   FAME / TIE-BREAKING. Each candidate has a `fame` count (`provider_count`
   distinct sources, `fame` total mentions) — how many independent registers
   returned it. When two or more candidates are otherwise comparable name
   matches, PREFER the one with higher fame: a company corroborated by many
   sources is far more likely the mainstream entity the user meant — especially
   when the query is a short/common name (e.g. "siemens") and the high-fame
   candidate is the full legal name ("Siemens Aktiengesellschaft"). Break any
   remaining tie by `completeness`. Never let fame override a clearly better name
   or jurisdiction match.

5. RECURSIVE TRIGGERING. If NONE of the candidates is the right entity, STRONGLY
   prefer "recursive_search" over "no_match": whenever you can think of a more
   promising query — an expanded acronym, the native-language registered name, a
   corrected spelling, or the well-known full legal name — return
   "recursive_search" with that name in `suggested_query` so the pipeline can
   re-query the registry with it. Examples:
     - query "BMW", candidates all unrelated small firms
       -> recursive_search, suggested_query "Bayerische Motoren Werke AG"
     - query "Deutsche Bahn Cargo" with nothing matching
       -> recursive_search, suggested_query "DB Cargo AG"
   Use "no_match" ONLY when you genuinely have no better query to offer.

6. ZERO CANDIDATES. The candidate list may be EMPTY (nothing survived the fuzzy
   filter). If you recognise the query as a real company, return
   "recursive_search" with its full registered legal name in the target
   jurisdiction (native form, including the legal suffix). Otherwise "no_match".

Keep `reasoning` to ONE short sentence — it is diagnostic metadata, never shown
as an answer. You MUST respond by calling the `submit_evaluation` tool exactly
once. Do not write any prose outside the tool call.
"""

# Forced-tool schema. Because we set tool_choice to this tool, Claude is
# guaranteed to return arguments matching this shape.
EVALUATION_TOOL: dict[str, Any] = {
    "name": "submit_evaluation",
    "description": (
        "Submit the semantic evaluation of the candidate list against the user "
        "query. Call this exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": [DECISION_MATCH, DECISION_NO_MATCH, DECISION_RECURSIVE],
                "description": (
                    "'match' if one candidate is the entity the user meant; "
                    "'no_match' if none match and no useful expansion is known; "
                    "'recursive_search' if the query is a known acronym/alias whose "
                    "expansion is absent from the candidates."
                ),
            },
            "winning_candidate_index": {
                "type": "integer",
                "description": (
                    "0-based index into the provided candidate list of the matching "
                    "record. Use -1 when decision is not 'match'."
                ),
            },
            "confidence": {
                "type": "number",
                "description": (
                    "Calibrated confidence in [0.0, 1.0] that the decision is correct. "
                    "For 'match', confidence that the chosen candidate is right."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Concise justification: note any acronym expansion, suffix "
                    "normalisation, or jurisdiction consideration that drove the decision."
                ),
            },
            "suggested_query": {
                "type": "string",
                "description": (
                    "Only for 'recursive_search': the expanded company name to re-query "
                    "the registry with (e.g. 'Bayerische Motoren Werke AG'). Empty otherwise."
                ),
            },
            "deciding_references": {
                "type": "array",
                "description": (
                    "Only for the web-search tie-break: the specific authoritative "
                    "references (Wikipedia, official site, business register, VAT/LEI "
                    "lookup, news) that established which candidate is correct. Cite the "
                    "concrete evidence — VAT/USt-IdNr, registration number, registry "
                    "court, LEI — that decided it. Empty when no web research was done."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Source URL."},
                        "title": {
                            "type": "string",
                            "description": (
                                "Short label, e.g. 'Handelsregister', 'Wikipedia', "
                                "'VAT lookup', 'GLEIF/LEI', 'Official site'."
                            ),
                        },
                        "detail": {
                            "type": "string",
                            "description": (
                                "The deciding fact this source proves, e.g. 'VAT "
                                "DE811907980 / HRB 6684 matches candidate [1]'."
                            ),
                        },
                    },
                    "required": ["url"],
                },
            },
        },
        "required": ["decision", "winning_candidate_index", "confidence", "reasoning"],
    },
}


# Anthropic server-side web search — enabled only for the tie-break disambiguation
# pass (executed by the API, not by us).
WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 5,
}

# The server-side web-search loop can return stop_reason="pause_turn" to continue
# searching; we resume up to this many times before giving up on the tie-break.
MAX_PAUSE_TURN_CONTINUATIONS = 5

# A tie = several candidates at near-identical (≈100%) confidence that the
# deterministic layers (name + fame) could not separate.
_TIE_HIGH = 0.97
_TIE_EPS = 0.02


def _is_tie(fuzz_candidates: list[dict[str, Any]]) -> bool:
    """True when ≥2 candidates sit at the top within _TIE_EPS and ≥ _TIE_HIGH."""
    confs = sorted(
        (float(c.get(CONFIDENCE_FIELD) or 0.0) for c in fuzz_candidates), reverse=True
    )
    if len(confs) < 2:
        return False
    return confs[0] >= _TIE_HIGH and (confs[0] - confs[1]) <= _TIE_EPS


def _build_disambiguation_content(
    user_query: str, target_jurisdiction: str, fuzz_candidates: list[dict[str, Any]]
) -> str:
    """The tie-break prompt: same candidates, plus a directive to web-research."""
    base = _build_user_content(user_query, target_jurisdiction, fuzz_candidates)
    directive = (
        "\n\nDISAMBIGUATION REQUIRED — several candidates are tied (all ~100% name "
        "matches) and the deterministic layers could not separate them. "
        "You MUST use the web_search tool (general web / Google) to establish which "
        "one the user means. Research, in order of authority:\n"
        "  1. Wikipedia and the company's official website — which legal entity is "
        "the well-known / primary one for this name in the jurisdiction.\n"
        "  2. The official business register and the VAT identifier (e.g. USt-IdNr / "
        "VAT number) or LEI — confirm the registration number, registry court and "
        "registered address of the intended entity.\n"
        "  3. News / sector context that distinguishes same-named companies.\n"
        "Search concrete terms such as the company name + jurisdiction + 'VAT' / "
        "'Handelsregister' / 'registration number' / 'LEI'. Then pick the matching "
        "candidate and call submit_evaluation exactly once. You MUST:\n"
        "  - cite in `reasoning` the single piece of evidence (VAT / registration "
        "number) that decided it, and\n"
        "  - populate `deciding_references` with the authoritative sources you used "
        "(URL + a short title + the deciding fact each one proves).\n"
        "If your research shows the correct entity is NOT among the candidates, use "
        "recursive_search."
    )
    return base + directive


def _build_user_content(
    user_query: str, target_jurisdiction: str, fuzz_candidates: list[dict[str, Any]]
) -> str:
    """Render the query + candidates into a single deterministic prompt string.

    Candidates are passed as compact JSON with their list index so the model can
    refer back to them by ``winning_candidate_index``.
    """
    lines = [
        f"USER QUERY (company name): {user_query!r}",
        f"TARGET JURISDICTION: {target_jurisdiction!r}",
        "",
        f"CANDIDATES ({len(fuzz_candidates)}), 0-based index shown:",
    ]
    for i, cand in enumerate(fuzz_candidates):
        # Surface the fields that matter for the decision; include the whole
        # record as JSON so the model has full context.
        name = cand.get(NAME_FIELD)
        juris = cand.get(JURISDICTION_FIELD)
        conf = cand.get(CONFIDENCE_FIELD)
        fame = cand.get("_fame", 1)
        providers = cand.get("_provider_count", 1)
        completeness = cand.get("_completeness", 0.0)
        lines.append(
            f"  [{i}] name={name!r} jurisdiction={juris!r} "
            f"confidence={conf!r} fame={fame} provider_count={providers} "
            f"completeness={completeness}"
        )
        lines.append(f"      full_record={json.dumps(cand, ensure_ascii=False)}")
    lines.append("")
    lines.append("Evaluate and call submit_evaluation.")
    return "\n".join(lines)


def _extract_tool_input(message: anthropic.types.Message) -> dict[str, Any]:
    """Pull the single ``submit_evaluation`` tool call out of the response.

    Raises SemanticFilterError on refusal, missing tool call, or malformed input.
    """
    if message.stop_reason == "refusal":
        raise SemanticFilterError(
            "Model refused to evaluate the request (stop_reason='refusal')."
        )

    for block in message.content:
        if block.type == "tool_use" and block.name == EVALUATION_TOOL["name"]:
            tool_input = block.input
            # The SDK already parses tool input into a dict; guard anyway so a
            # malformed payload surfaces as our typed error rather than a later
            # KeyError/TypeError.
            if not isinstance(tool_input, dict):
                try:
                    tool_input = json.loads(tool_input)  # defensive
                except (TypeError, json.JSONDecodeError) as exc:
                    raise SemanticFilterError(
                        f"Tool input was not valid JSON: {exc}"
                    ) from exc
            return tool_input

    raise SemanticFilterError(
        f"Response contained no '{EVALUATION_TOOL['name']}' tool call "
        f"(stop_reason={message.stop_reason!r})."
    )


def _citations_from_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    """Pull the URLs Claude actually consulted out of response content blocks.

    Reads both the server-side ``web_search_tool_result`` blocks (the pages the
    search returned) and any ``citations`` attached to answer text. Errors and
    malformed blocks are skipped. Accepts a flat list of blocks so it can span
    several ``pause_turn`` continuations. Returns ``[{url, title, detail}, …]``
    deduped by URL — the references the model had in front of it when deciding.
    """
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(url: Any, title: Any, detail: Any) -> None:
        if not isinstance(url, str) or not url or url in seen:
            return
        seen.add(url)
        refs.append({"url": url, "title": (title if isinstance(title, str) and title else url), "detail": detail if isinstance(detail, str) else None})

    for block in blocks or []:
        btype = getattr(block, "type", None)
        if btype == "web_search_tool_result":
            content = getattr(block, "content", None)
            # On a search error this is an error object, not a list — skip it.
            if isinstance(content, list):
                for item in content:
                    _add(getattr(item, "url", None), getattr(item, "title", None), None)
        elif btype == "text":
            for cit in getattr(block, "citations", None) or []:
                _add(getattr(cit, "url", None), getattr(cit, "title", None), getattr(cit, "cited_text", None))
    return refs


def _extract_web_citations(message: anthropic.types.Message) -> list[dict[str, Any]]:
    """Convenience wrapper: citations from a single message's content blocks."""
    return _citations_from_blocks(list(getattr(message, "content", None) or []))


def _merge_references(
    model_refs: Any, consulted_refs: list[dict[str, Any]] | None, *, cap: int = 10
) -> list[dict[str, Any]]:
    """Combine the model's explicit deciding references with the consulted URLs.

    The model's ``deciding_references`` (it picked these as the evidence that
    settled the tie) come first and keep their ``detail``; any remaining pages
    the search actually surfaced are appended so the trail is auditable. Deduped
    by URL, capped at ``cap``.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(url: Any, title: Any, detail: Any) -> None:
        if not isinstance(url, str) or not url or url in seen:
            return
        seen.add(url)
        out.append({"url": url, "title": (title if isinstance(title, str) and title else url), "detail": detail if isinstance(detail, str) and detail else None})

    for r in model_refs or []:
        if isinstance(r, dict):
            _add(r.get("url"), r.get("title"), r.get("detail"))
    for r in consulted_refs or []:
        if isinstance(r, dict):
            _add(r.get("url"), r.get("title"), r.get("detail"))
    return out[:cap]


def _assemble_result(
    tool_input: dict[str, Any],
    fuzz_candidates: list[dict[str, Any]],
    *,
    consulted_references: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Map the validated tool input onto the final result (pure, no I/O).

    Returns a dict with:
      - decision: one of match/no_match/recursive_search
      - winning_candidate: the input record (same shape) with updated confidence,
        or None
      - confidence: the calibrated [0,1] confidence
      - reasoning: the model's justification
      - recursive_search: {"suggested_query": str} when a re-search is flagged,
        else None
    """
    decision = tool_input.get("decision")
    if decision not in (DECISION_MATCH, DECISION_NO_MATCH, DECISION_RECURSIVE):
        raise SemanticFilterError(f"Unexpected decision value: {decision!r}")

    # Clamp confidence into [0, 1]; treat a non-numeric value as an error.
    raw_conf = tool_input.get("confidence")
    try:
        confidence = round(min(max(float(raw_conf), 0.0), 1.0), 4)
    except (TypeError, ValueError) as exc:
        raise SemanticFilterError(f"Confidence was not numeric: {raw_conf!r}") from exc

    reasoning = str(tool_input.get("reasoning", ""))

    result: dict[str, Any] = {
        "decision": decision,
        "winning_candidate": None,
        "confidence": confidence,
        "reasoning": reasoning,
        "recursive_search": None,
        # The authoritative sources that decided a tie (VAT, register, Wikipedia,
        # LEI, …). Empty on the normal path where no web research was needed.
        "references": _merge_references(
            tool_input.get("deciding_references"), consulted_references
        ),
    }

    if decision == DECISION_MATCH:
        idx = tool_input.get("winning_candidate_index")
        if not isinstance(idx, int) or not (0 <= idx < len(fuzz_candidates)):
            raise SemanticFilterError(
                f"decision='match' but winning_candidate_index={idx!r} is out of "
                f"range for {len(fuzz_candidates)} candidates."
            )
        # Return the record in the same shape as the input, with confidence updated.
        winning = dict(fuzz_candidates[idx])
        winning[CONFIDENCE_FIELD] = confidence
        result["winning_candidate"] = winning

    elif decision == DECISION_RECURSIVE:
        suggested = str(tool_input.get("suggested_query") or "").strip()
        if not suggested:
            raise SemanticFilterError(
                "decision='recursive_search' but no suggested_query was provided."
            )
        result["recursive_search"] = {"suggested_query": suggested}

    return result


def _empty_result(reasoning: str) -> dict[str, Any]:
    return {
        "decision": DECISION_NO_MATCH,
        "winning_candidate": None,
        "confidence": 0.0,
        "reasoning": reasoning,
        "recursive_search": None,
        "references": [],
    }


def _mock_result(fuzz_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic offline fallback: pick the top fuzzy candidate as the match.

    Used when ``mock=True`` (PIPELINE_MOCK / no API key) so the chain runs
    end-to-end with no network call. The RapidFuzz layer has already sorted the
    candidates by confidence, so candidate 0 is the strongest.
    """
    if not fuzz_candidates:
        return _empty_result("No candidates were provided by the fuzzy filter (mock).")
    winning = dict(fuzz_candidates[0])
    confidence = float(winning.get(CONFIDENCE_FIELD) or 0.0)
    return {
        "decision": DECISION_MATCH,
        "winning_candidate": winning,
        "confidence": round(min(max(confidence, 0.0), 1.0), 4),
        "reasoning": "Mock mode: selected the top RapidFuzz candidate without calling the LLM.",
        "recursive_search": None,
        "references": [],
    }


def _try_web_disambiguation(
    client: anthropic.Anthropic,
    user_query: str,
    target_jurisdiction: str,
    fuzz_candidates: list[dict[str, Any]],
    model: str,
    max_tokens: int,
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Resolve a tie by letting Claude web-search (Wikipedia, registry, VAT, LEI).

    Returns ``(submit_evaluation tool input, consulted references)``, or ``None``
    on any failure (web search not enabled on the key, no tool call returned, API
    error) so the caller can fall back to the standard forced evaluation. The
    consulted references are the URLs the search surfaced, so the deciding
    evidence can be linked back in the final result.
    """
    content = _build_disambiguation_content(user_query, target_jurisdiction, fuzz_candidates)
    # Server-side web search needs headroom: it pauses (pause_turn) to run each
    # query, so allow a longer timeout than the plain forced eval.
    client = client.with_options(timeout=max(timeout, 120.0))
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
    # Accumulate content blocks across pause_turn continuations so we can pull
    # citations from every search turn, not just the final one.
    collected_blocks: list[Any] = []
    message: anthropic.types.Message | None = None
    try:
        for _ in range(MAX_PAUSE_TURN_CONTINUATIONS):
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                # Both tools, choice 'auto': the model web-searches (server-side)
                # and then calls submit_evaluation. Forcing the eval tool would
                # block the search, so we instruct-and-extract instead.
                tools=[WEB_SEARCH_TOOL, EVALUATION_TOOL],
                tool_choice={"type": "auto"},
                messages=messages,
            )
            collected_blocks.extend(getattr(message, "content", None) or [])
            if message.stop_reason == "pause_turn":
                # Web-search loop paused — append the turn and resume it.
                messages = messages[:1] + [
                    {"role": "assistant", "content": message.content}
                ]
                continue
            break
        else:
            logger.warning(
                "web-search disambiguation exceeded pause_turn budget; using standard eval"
            )
            return None
    except anthropic.APIError as exc:
        logger.warning("web-search disambiguation unavailable (%s); using standard eval", exc)
        return None
    try:
        tool_input = _extract_tool_input(message)
    except SemanticFilterError as exc:
        logger.warning("web-search disambiguation produced no evaluation (%s)", exc)
        return None
    return tool_input, _citations_from_blocks(collected_blocks)


def semantic_filter(
    user_query: str,
    target_jurisdiction: str,
    fuzz_candidates: list[dict[str, Any]],
    *,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2048,
    timeout: float = 60.0,
    mock: bool = False,
) -> dict[str, Any]:
    """Run the LLM semantic filter over the RapidFuzz candidate list.

    Parameters
    ----------
    user_query:
        The original company name the user searched for, e.g. "BMW".
    target_jurisdiction:
        Two-letter jurisdiction code, e.g. "DE".
    fuzz_candidates:
        The list of registry-record dicts emitted by the RapidFuzz layer.
    client:
        An optional pre-configured ``anthropic.Anthropic`` instance. If omitted,
        one is constructed (reads ANTHROPIC_API_KEY from the environment).
    model:
        Claude model id. Defaults to the most capable model.
    max_tokens / timeout:
        Generation cap and per-request timeout (seconds).
    mock:
        Skip the API call and deterministically pick the top fuzzy candidate.

    Returns
    -------
    dict
        See ``_assemble_result`` for the shape. The ``winning_candidate`` (when
        present) is in the same schema as the input records, with a recalibrated
        ``confidence``.

    Raises
    ------
    SemanticFilterError
        On API failure, model refusal, or an unparseable/invalid response.
    """
    if mock:
        # _mock_result falls back to no_match on an empty candidate list.
        return _mock_result(fuzz_candidates)

    # NOTE: an EMPTY candidate list still goes to the LLM — that is the
    # recursive case: "nothing survived; do you know a better query?" The model
    # answers recursive_search + suggested_query when it recognises the name.

    if client is None:
        client = anthropic.Anthropic()

    # Tie-break with web research: when several candidates are ~100% matches that
    # name + fame could not separate, let Claude search the web (Wikipedia,
    # official site, registry, VAT number / LEI) to establish the correct one.
    # Any failure (web search unavailable, no tool call) falls back to the plain
    # forced evaluation below.
    if _is_tie(fuzz_candidates):
        tie = _try_web_disambiguation(
            client, user_query, target_jurisdiction, fuzz_candidates, model, max_tokens, timeout
        )
        if tie is not None:
            tie_input, tie_refs = tie
            logger.info(
                "tie resolved via web-search disambiguation (%d references)", len(tie_refs)
            )
            return _assemble_result(
                tie_input, fuzz_candidates, consulted_references=tie_refs
            )

    user_content = _build_user_content(
        user_query, target_jurisdiction, fuzz_candidates
    )

    try:
        message = client.with_options(timeout=timeout).messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            tools=[EVALUATION_TOOL],
            # Force the tool: the model must return submit_evaluation arguments,
            # guaranteeing a schema-shaped response with no prose to parse.
            tool_choice={"type": "tool", "name": EVALUATION_TOOL["name"]},
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.APITimeoutError as exc:
        raise SemanticFilterError(f"Anthropic API timed out after {timeout}s.") from exc
    except anthropic.RateLimitError as exc:
        raise SemanticFilterError("Anthropic API rate limit exceeded.") from exc
    except anthropic.APIConnectionError as exc:
        raise SemanticFilterError(f"Could not reach the Anthropic API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        # Covers 4xx/5xx with a structured status code.
        raise SemanticFilterError(
            f"Anthropic API error {exc.status_code}: {exc.message}"
        ) from exc

    tool_input = _extract_tool_input(message)
    logger.debug("semantic_filter raw tool input: %s", tool_input)
    return _assemble_result(tool_input, fuzz_candidates)
