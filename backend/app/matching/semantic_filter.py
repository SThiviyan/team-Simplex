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

5. RECURSIVE TRIGGERING. If NONE of the candidates is the right entity, decide
   between two outcomes:
     - "no_match": the query is not a recognisable alias of anything, OR you have
       no confident expansion to offer. Set confidence low.
     - "recursive_search": the query IS a well-known acronym/alias, but its
       expanded legal name is NOT present among the candidates. Provide the
       expanded name in `suggested_query` so the pipeline can re-query the
       registry. Example: query "BMW" with candidates that are all unrelated
       small firms -> recursive_search, suggested_query "Bayerische Motoren Werke AG".

You MUST respond by calling the `submit_evaluation` tool exactly once. Do not
write any prose outside the tool call.

6. WHEN TO MATCH vs ABSTAIN. A SINGLE candidate whose core name matches the
   query (ignoring legal-suffix differences) IS a match — return it, do not
   abstain just because it is the only option or is a small/obscure company.
   Only return no_match/ambiguous when:
     - TWO OR MORE genuinely DISTINCT entities fit equally well and nothing in
       the query separates them, OR
     - no candidate's core name actually matches the query.
   A real registered entity that no one else can be confused with should be
   matched even at a small risk — abstaining on a clear single hit is itself an
   error.

7. DON'T PICK A DIFFERENTLY-NAMED SIBLING. Companies in the same family often
   differ by ONE meaning-bearing word: "X Trust Ltd" vs "X Services Ltd",
   "X Holding" vs "X Operating", "X Group" vs "X Management". These are
   DIFFERENT legal entities. Do NOT select a candidate whose core name differs
   from the query by such a word unless the query itself contains that word.
   If the only candidates are mismatched siblings, prefer no_match/ambiguous
   over confidently returning the wrong entity.

CORPORATE GROUPS. When a bare brand query matches several entities of one group
(holding plc vs operating DAC/A-S/GmbH subsidiaries), prefer the group's PRIMARY
registered entity — the top holding / listed company — unless the query names a
subsidiary explicitly. This is a tie-breaker among multiple group entities, NOT
a reason to abstain when only one clear entity is present.

8. BRAND FAME — the bare-name default. A bare, well-known company/brand name
   ("McKinsey", "Bosch", "Allianz", "Deloitte") means the GLOBALLY WELL-KNOWN
   company of that name — its principal operating/parent entity — NOT an obscure,
   unrelated small company that merely shares the bare word. Among same-named
   candidates this OUTRANKS exact string proximity:
     - A candidate that the well-known firm is actually known as — e.g.
       "McKinsey & Company, Inc. United Kingdom" — beats a bare "<Brand> Ltd"
       shell ("McKinsey Ltd") even though "McKinsey Ltd" matches the query string
       more closely.
     - Use the shortlist's own evidence: if a Wikipedia/Wikidata candidate (check
       full_record.source / provider) names a specific entity ("McKinsey &
       Company"), that is the notability anchor for which entity the user means;
       pick the register candidate that corresponds to it.
   This refines rule 7: for a bare famous-brand query, the principal entity's
   extra words ("& Company", "Group", "Holding") are NOT a disqualifying sibling
   difference — they identify the real company. Only fall back to ambiguous if no
   candidate corresponds to the famous entity and several obscure ones tie.
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
        },
        "required": ["decision", "winning_candidate_index", "confidence", "reasoning"],
    },
}


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
        lines.append(
            f"  [{i}] name={name!r} jurisdiction={juris!r} "
            f"fuzzy_confidence={conf!r}"
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


def _assemble_result(
    tool_input: dict[str, Any], fuzz_candidates: list[dict[str, Any]]
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
    }

    if decision == DECISION_MATCH:
        idx = tool_input.get("winning_candidate_index")
        if not isinstance(idx, int) or not (0 <= idx < len(fuzz_candidates)):
            raise SemanticFilterError(
                f"decision='match' but winning_candidate_index={idx!r} is out of "
                f"range for {len(fuzz_candidates)} candidates."
            )
        # Confidence floor: if the verifier itself is not confident (its own
        # rating is below the floor — the prompt's "plausible but ambiguous"
        # band), do NOT emit the match. Abstaining is correct: a wrong confident
        # answer scores worse than a blank. Uses the LLM's own uncertainty signal.
        from app.config import settings

        if confidence < settings.min_match_confidence:
            result["decision"] = DECISION_NO_MATCH
            result["reasoning"] = (
                f"Match confidence {confidence} below floor "
                f"{settings.min_match_confidence}; abstained. {reasoning}"
            )
            return result
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
    }


# Shared default client: the API is stateless (every request carries its own
# full message list), so one client is just connection-pool reuse. The sync
# client is thread-safe, and the per-request with_options(timeout=...) below
# keeps timeouts out of client construction. Callers can still inject their own.
_shared_client: anthropic.Anthropic | None = None


def _default_client() -> anthropic.Anthropic:
    global _shared_client
    if _shared_client is None:
        _shared_client = anthropic.Anthropic()
    return _shared_client


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
    force_llm_on_empty: bool = False,
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
    if not fuzz_candidates and not force_llm_on_empty:
        # Nothing to evaluate — short-circuit without burning an API call.
        # Callers set force_llm_on_empty for abbreviation-shaped queries, where
        # the LLM can still flag recursive_search with an expanded name even
        # though zero candidates survived the fuzzy gate.
        return _empty_result("No candidates were provided by the fuzzy filter.")

    if mock:
        return _mock_result(fuzz_candidates)

    if client is None:
        client = _default_client()

    user_content = _build_user_content(
        user_query, target_jurisdiction, fuzz_candidates
    )

    try:
        message = client.with_options(timeout=timeout).messages.create(
            model=model,
            max_tokens=max_tokens,
            # Static across every row of a batch — one cache breakpoint lets
            # rows after the first read system + tool schema from cache.
            system=[
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
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
