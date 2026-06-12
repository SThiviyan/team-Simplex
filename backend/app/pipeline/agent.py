"""Layer 1 — DB search agent.

Walks the country's ranked MCP server list top to bottom; per entry the Claude
agent connects to that MCP endpoint (in-memory for our per-country buckets,
streamable HTTP for external servers), discovers its tools, and runs a tool-use
loop to find the registry entry. Every tool result is captured as a trace —
both as evidence for the grounding check (an ID the tools never returned is
blanked, never submitted) and as record material for the matching layer.

When a country's MCP attempts yield nothing usable, a single web-search call is
the fallback. Structured JSON output is enforced via output_config throughout.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field

import anthropic

from app.config import settings
from app.pipeline import event_log
from app.pipeline.mcp_client import anthropic_tools, call_tool, open_session
from app.pipeline.models import ExtractionPayload, ExtractionResult, McpServerEntry, QueryRow

logger = logging.getLogger(__name__)

MAX_PAUSE_TURN_CONTINUATIONS = 5
# Hardest observed case (PwC) needed 3 rounds; 5 leaves headroom while stopping
# a confused agent from burning 8 Opus calls on one row. On cap-hit the entry
# returns None and the walk/fallback continues exactly as before.
MAX_TOOL_ROUNDS = 5

# no_match_reason prefix marking "the pipeline errored", as opposed to a genuine
# registry no-match (not_in_registry / ambiguous_candidates / out_of_scope).
LAYER1_ERROR_PREFIX = "layer1_error"
UNGROUNDED_REASON = "ungrounded_registry_id"

_OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": ExtractionPayload.model_json_schema(),
}

# Prompt cache (5-min TTL): the system prompt + tool schemas are identical for
# every round of every row, so one breakpoint on the system block makes each
# call after the first read that prefix from cache. Responses are unaffected.
_CACHE = {"type": "ephemeral"}


def _system_blocks() -> list[dict]:
    return [{"type": "text", "text": _SYSTEM, "cache_control": _CACHE}]


def _move_conversation_breakpoint(messages: list[dict]) -> None:
    """Keep exactly one moving cache breakpoint on the newest tool_result block
    (so each round re-reads the growing conversation from cache instead of
    re-processing it). Older breakpoints are removed — the API allows max 4."""
    last = None
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                block.pop("cache_control", None)
                last = block
    if last is not None:
        last["cache_control"] = _CACHE

_SYSTEM = """You are a company-registry research agent in a KYB pipeline.

Given a company name and a country code, find the company's official commercial-register
entry using the search tools available to you. Do not answer from memory alone.

Search strategy — input names are messy (partial, abbreviated, transliterated, trading
names, sometimes a wrong jurisdiction):
- Start with the name as given. If results are poor, reformulate and retry: strip or
  append legal forms (GmbH, AG, Ltd, plc, S.A., ...), expand known abbreviations
  (e.g. BMW -> Bayerische Motoren Werke), try transliteration variants.
- If the registry evidence points to a different jurisdiction than requested, say so via
  jurisdiction_confirmed rather than forcing a match.

Sub-jurisdictional structure:
- Germany/Austria: registration numbers are only unique per court — always capture the
  Registergericht/Firmenbuchgericht (registry_court). HRA = partnerships/sole traders,
  HRB = corporations (GmbH, AG).
- USA: companies register per state (Secretary of State); there is no federal registry.
- Canada: federal plus provincial registries; UAE: emirate-level registries.

Sole proprietors / freelancers: in many places (UK sole traders, Irish sole traders,
Spanish autónomos, US sole proprietors) the correct answer is NO registry entry — return
null fields with no_match_reason 'not_in_registry'. Others DO register them (Poland
CEIDG, France SIREN, Czech Živnostenský rejstřík, Belgium KBO/BCE).

Output rules:
- registry_id: the official NATIONAL registration number EXACTLY as a tool result
  returned it. NEVER write a registration number that did not literally appear in a
  tool result — an invented ID is the worst possible answer; a blank one is neutral.
- NOT registry numbers (never put these in registry_id): an LEI (20-char GLEIF code),
  a Wikidata QID, an SEC CIK, a stock ticker. GLEIF results carry the entity's real
  national number as 'national registry number' / registered_as — use THAT. If the
  only identifier the tools returned is an LEI/QID/CIK, registry_id stays null.
- registry_court: the specific court or registry office (required for DE, AT, etc.).
- name_normalized_register_name: the FULL legal name as registered ('Sinpex GmbH',
  not 'Sinpex').
- jurisdiction_confirmed: the confirmed country or state/province, only if the registry
  evidence confirms it; else null. Use the country code at the same granularity as the
  request (DE, not DE-BY) — sub-national codes like US-CA only where companies register
  at state/province level (USA, Canada).
- If several DISTINCT legal entities match the query equally well and nothing in the
  query disambiguates them, do not pick one: no_match_reason 'ambiguous_candidates'.
- Tier A/B enrichment (registered_address, incorporation_date, organization_type,
  status, officers): fill ONLY values that literally appear in the tool results for
  THIS entity — wrong enrichment is penalized, blank is neutral. Dates as YYYY-MM-DD.
  status as one of: active / dissolved / in_liquidation / dormant. Null when unknown.
- confidence: a number in [0, 1], used for calibration scoring — be honest. Only go
  above 0.8 when registry_id and the registered name both clearly match the query.
- source: at least one citable URL or registry document reference from the tool results.
- If there is no confident match, set the data fields to null and no_match_reason to:
  'not_in_registry', 'ambiguous_candidates', 'out_of_scope', or another short
  snake_case label.
- reasoning: one or two sentences on why this confidence.
- After your final tool call, respond with the JSON object only."""


@dataclass
class Layer1Outcome:
    """Everything Layer 1 produced for one query."""

    candidates: list[ExtractionResult] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)  # matching-layer shaped
    trace: list[dict] = field(default_factory=list)  # tool calls + outputs
    errors: list[str] = field(default_factory=list)

    def error_result(self, query_id: str) -> ExtractionResult | None:
        if self.candidates or not self.errors:
            return None
        return ExtractionResult(
            query_id=query_id,
            confidence=0.0,
            no_match_reason=f"{LAYER1_ERROR_PREFIX}: {'; '.join(self.errors)}",
        )


# One client per event loop (CLI, server, and each test run their own): the
# API is stateless — every call carries its own full `messages` — so sharing a
# client only reuses pooled connections; httpx pools are loop-bound, hence the
# loop key (same pattern as app/integrations/http.py).
_clients: dict[int, anthropic.AsyncAnthropic] = {}


def _client() -> anthropic.AsyncAnthropic:
    loop_id = id(asyncio.get_running_loop())
    client = _clients.get(loop_id)
    if client is None:
        # 4 retries (~30s of backoff) balances riding out brief 529 blips
        # against stalling a row for minutes: on a sustained storm we fail
        # over to record salvage / web search instead of retrying forever.
        client = anthropic.AsyncAnthropic(max_retries=4)
        _clients[loop_id] = client
    return client


def _user_prompt(query: QueryRow, via: str) -> str:
    return (
        f"Company name (search query): {query.name}\n"
        f"Country code (jurisdiction): {query.jurisdiction}\n"
        f"You are connected to: {via}"
    )


def _mock_payload(query: QueryRow) -> ExtractionPayload:
    return ExtractionPayload(
        registry_id=f"MOCK-{query.query_id}",
        registry_court=f"Mock court ({query.jurisdiction})",
        name_normalized_register_name=query.name,
        jurisdiction_confirmed=query.jurisdiction,
        confidence=0.9,
        source="mock://pipeline",
        no_match_reason=None,
        registered_address=None,
        incorporation_date=None,
        organization_type=None,
        status=None,
        officers=None,
        reasoning="mock mode",
    )


def _extract_payload(response) -> ExtractionPayload:
    text = next(b.text for b in reversed(response.content) if b.type == "text")
    return ExtractionPayload.model_validate_json(text)


def _match_record(query_id: str, r: dict) -> dict | None:
    """SearchResult dump (from an MCP tool result) -> matching-layer record."""
    name = r.get("register_name") or r.get("title")
    if not name:
        return None
    return {
        "query_id": query_id,
        "registry_id": r.get("registry_id"),
        "registry_court": r.get("registry_court"),
        "name_normalized_register_name": name,
        "jurisdiction_confirmed": r.get("jurisdiction"),
        "confidence": float(r.get("score") or 0.0),
        "source": r.get("url") or r.get("source"),
        "no_match_reason": None,
        "provider": r.get("source"),
        "snippet": r.get("snippet"),
        # Tier A material + cross-reference codes for the Layer-2 enrichment.
        "address": r.get("address"),
        "organization_type": r.get("organization_type"),
        "status": r.get("status"),
        "last_update": r.get("last_update"),
        "metadata": r.get("metadata") or {},
    }


def _records_from_structured(query_id: str, structured) -> list[dict]:
    """Pull company records out of a tool's structured output."""
    if isinstance(structured, dict):
        items = structured.get("results") or structured.get("result") or []
    elif isinstance(structured, list):
        items = structured
    else:
        items = []
    records = []
    for item in items:
        if isinstance(item, dict):
            rec = _match_record(query_id, item)
            if rec:
                records.append(rec)
    return records


_ALNUM = re.compile(r"[^a-z0-9]")

# Identifier shapes that are real codes but NOT company registration numbers.
# They appear in tool output, so grounding alone cannot catch them.
NON_REGISTRY_REASON = "non_registry_identifier"
_NON_REGISTRY_ID_SHAPES = [
    # LEI: 4-char prefix, "00" reserved chars, 12 alnum, 2 check digits.
    ("lei", re.compile(r"^[A-Z0-9]{4}00[A-Z0-9]{12}\d{2}$")),
    ("wikidata_qid", re.compile(r"^Q\d{1,10}$")),
    # SEC CIK as EDGAR renders it: 10 digits, zero-padded. (National numbers
    # like UK '00445790' are 8-9 digits, so they don't match.)
    ("sec_cik", re.compile(r"^0\d{9}$")),
]


def _non_registry_id_kind(registry_id: str) -> str | None:
    value = registry_id.strip().upper().replace(" ", "")
    for kind, pattern in _NON_REGISTRY_ID_SHAPES:
        if pattern.match(value):
            return kind
    return None


def _grounded(registry_id: str, trace: list[dict]) -> bool:
    """True iff the registry_id literally appeared in some tool output."""
    needle = _ALNUM.sub("", registry_id.lower())
    if not needle:
        return False
    for entry in trace:
        haystack = _ALNUM.sub("", str(entry.get("output", "")).lower())
        if needle in haystack:
            return True
    return False


def _ground_source(payload: ExtractionPayload, trace: list[dict]) -> ExtractionPayload:
    """A cited URL must have literally appeared in a tool result — a fabricated
    link is as bad as a fabricated ID. Replace an ungrounded URL with the
    registry document reference (court + number), which IS tool-backed because
    the registry_id grounding check already passed."""
    source = payload.source or ""
    if not source.startswith(("http://", "https://")) or _grounded(source, trace):
        return payload
    doc_ref = " ".join(x for x in (payload.registry_court, payload.registry_id) if x) or None
    return payload.model_copy(
        update={
            "source": doc_ref,
            "reasoning": f"Cited URL {source!r} did not appear in any tool result; "
            "replaced with the registry document reference. " + payload.reasoning,
        }
    )


def apply_grounding(
    payload: ExtractionPayload, trace: list[dict], *, web_search: bool = False
) -> tuple[ExtractionPayload, bool]:
    """Blank an ID the tools never returned. Web-search results can't be checked
    against a trace (the search runs server-side), so they pass through."""
    # Wrong identifier TYPE (LEI / QID / CIK) is checked regardless of transport:
    # these are grounded in tool/web output but are not registration numbers.
    kind = _non_registry_id_kind(payload.registry_id) if payload.registry_id else None
    if kind:
        return payload.model_copy(
            update={
                "registry_id": None,
                "registry_court": None,
                "confidence": 0.0,
                "source": None,
                "no_match_reason": NON_REGISTRY_REASON,
                "reasoning": f"Blanked: {payload.registry_id!r} is a {kind}, not a company "
                "registration number. " + payload.reasoning,
            }
        ), False
    if web_search:
        return payload, True
    if not payload.registry_id or _grounded(payload.registry_id, trace):
        return _ground_source(payload, trace), True
    blanked = payload.model_copy(
        update={
            "registry_id": None,
            "registry_court": None,
            "name_normalized_register_name": None,
            "jurisdiction_confirmed": None,
            "confidence": 0.0,
            "source": None,
            "no_match_reason": UNGROUNDED_REASON,
            "reasoning": f"Blanked: claimed registry_id {payload.registry_id!r} "
            "does not appear in any tool result. " + payload.reasoning,
        }
    )
    return blanked, False


PREGATHER_TOOL = "search_companies"


async def _pregather(session, query, entry, outcome, run_id, tools) -> str | None:
    """Run the obvious first search deterministically, before any model call.

    Round 0 used to be a forced tool-use turn whose only job was asking for
    this exact search — one full Opus round-trip spent on a foregone
    conclusion. The output lands in the trace, so grounding sees it exactly
    as if the agent had requested it."""
    if not any(t["name"] == PREGATHER_TOOL for t in tools):
        return None
    args = {"name": query.name, "limit": 10}
    try:
        text, structured = await call_tool(session, PREGATHER_TOOL, args)
    except Exception:
        return None  # endpoint hiccup — fall back to the forced agent search
    if structured is None and text.startswith("Tool error"):
        return None
    records = _records_from_structured(query.query_id, structured)
    outcome.records.extend(records)
    outcome.trace.append(
        {"endpoint": entry.url, "tool": PREGATHER_TOOL, "input": args, "output": text}
    )
    await event_log.log_event(
        run_id, "tool_result", query.query_id,
        tool=PREGATHER_TOOL, record_count=len(records), pregather=True,
        top_hits=[r["name_normalized_register_name"] for r in records[:3]],
    )
    return text


async def _mcp_attempt(
    client: anthropic.AsyncAnthropic,
    query: QueryRow,
    entry: McpServerEntry,
    outcome: Layer1Outcome,
    run_id: str,
) -> ExtractionPayload | None:
    """One extraction attempt against a single MCP endpoint, with tool loop."""
    async with open_session(entry.url, entry.auth_token or None) as session:
        tools = anthropic_tools(await session.list_tools())
        await event_log.log_event(
            run_id, "mcp_connected", query.query_id,
            endpoint=entry.url, server=entry.name, tools=[t["name"] for t in tools],
        )
        pregathered = await _pregather(session, query, entry, outcome, run_id, tools)
        user = _user_prompt(query, f"MCP server '{entry.name}'")
        if pregathered is not None:
            user += (
                f"\n\n{PREGATHER_TOOL} has already been called with the name as given; "
                f"its results:\n{pregathered}\n\n"
                "If these results already identify the registry entry, answer directly. "
                "Search again (reformulated name, legal-form variants, single sources) "
                "only if they do not."
            )
        messages = [{"role": "user", "content": user}]

        for round_idx in range(MAX_TOOL_ROUNDS):
            kwargs: dict = dict(
                model=settings.anthropic_model,
                max_tokens=16000,
                system=_system_blocks(),
                messages=messages,
                tools=tools,
            )
            if round_idx == 0 and pregathered is None:
                # No pre-gathered evidence: the agent MUST search before
                # answering — an un-searched answer is ungrounded by
                # construction and would only get blanked.
                kwargs["tool_choice"] = {"type": "any"}
            else:
                kwargs["output_config"] = {"format": _OUTPUT_FORMAT}
            response = await client.messages.create(**kwargs)

            if response.stop_reason == "refusal":
                await event_log.log_event(run_id, "error", query.query_id, kind="refusal")
                return None

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "tool_use" and tool_uses:
                messages.append({"role": "assistant", "content": response.content})
                for block in tool_uses:
                    await event_log.log_event(
                        run_id, "tool_call", query.query_id,
                        endpoint=entry.url, tool=block.name, arguments=block.input,
                    )
                # The model fans out independent searches in one round — run
                # them concurrently; results still attach by tool_use_id.
                outputs = await asyncio.gather(
                    *(call_tool(session, b.name, dict(b.input)) for b in tool_uses)
                )
                tool_results = []
                for block, (text, structured) in zip(tool_uses, outputs):
                    records = _records_from_structured(query.query_id, structured)
                    outcome.records.extend(records)
                    outcome.trace.append(
                        {"endpoint": entry.url, "tool": block.name,
                         "input": block.input, "output": text}
                    )
                    await event_log.log_event(
                        run_id, "tool_result", query.query_id,
                        tool=block.name, record_count=len(records),
                        top_hits=[r["name_normalized_register_name"] for r in records[:3]],
                    )
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": text}
                    )
                messages.append({"role": "user", "content": tool_results})
                _move_conversation_breakpoint(messages)
                continue

            return _extract_payload(response)

    logger.warning("MCP attempt for %s exceeded tool-round budget", query.query_id)
    return None


async def _web_search_attempt(
    client: anthropic.AsyncAnthropic, query: QueryRow, run_id: str
) -> ExtractionPayload | None:
    """Fallback: one server-side web-search call (no MCP endpoint usable)."""
    await event_log.log_event(run_id, "web_search_fallback", query.query_id)
    messages = [{"role": "user", "content": _user_prompt(query, "web search")}]

    for _ in range(MAX_PAUSE_TURN_CONTINUATIONS):
        response = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=16000,
            system=_system_blocks(),
            messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            output_config={"format": _OUTPUT_FORMAT},
        )
        if response.stop_reason == "pause_turn":
            messages = messages[:1] + [{"role": "assistant", "content": response.content}]
            continue
        if response.stop_reason == "refusal":
            await event_log.log_event(run_id, "error", query.query_id, kind="refusal")
            return None
        return _extract_payload(response)

    logger.warning("web-search attempt for %s exceeded pause_turn budget", query.query_id)
    return None


async def run_layer1(query: QueryRow, mcps: list[McpServerEntry], run_id: str) -> Layer1Outcome:
    """Walk the ranked MCP list top to bottom; collect candidates + evidence."""
    outcome = Layer1Outcome()

    if settings.pipeline_mock:
        payload = _mock_payload(query)
        outcome.candidates.append(ExtractionResult.from_payload(query.query_id, payload))
        await event_log.log_event(
            run_id, "agent_answer", query.query_id,
            confidence=payload.confidence, reasoning=payload.reasoning, mock=True,
        )
        return outcome

    client = _client()
    usable = [m for m in mcps if not m.is_placeholder]

    for entry in usable:
        await event_log.log_event(
            run_id, "mcp_selected", query.query_id,
            country=query.jurisdiction, endpoint=entry.url, rank=entry.rank,
        )
        try:
            payload = await _mcp_attempt(client, query, entry, outcome, run_id)
        except Exception as exc:
            logger.exception("MCP attempt failed for %s via %s", query.query_id, entry.url)
            outcome.errors.append(f"{entry.name}: {type(exc).__name__}: {str(exc)[:120]}")
            await event_log.log_event(
                run_id, "error", query.query_id, kind=type(exc).__name__, endpoint=entry.url
            )
            continue
        if payload is None:
            continue

        payload, grounded = apply_grounding(payload, outcome.trace)
        await event_log.log_event(
            run_id, "grounding_check", query.query_id,
            registry_id=payload.registry_id, grounded=grounded,
        )
        await event_log.log_event(
            run_id, "agent_answer", query.query_id,
            registry_id=payload.registry_id, confidence=payload.clamped_confidence(),
            reasoning=payload.reasoning, endpoint=entry.url,
        )
        outcome.candidates.append(ExtractionResult.from_payload(query.query_id, payload))

        if payload.clamped_confidence() >= settings.confidence_threshold:
            return outcome  # early exit — skip lower-ranked MCPs

    # Web-search fallback: nothing usable came out of the MCP walk.
    if not outcome.candidates and not outcome.records:
        try:
            payload = await _web_search_attempt(client, query, run_id)
        except Exception as exc:
            logger.exception("web-search fallback failed for %s", query.query_id)
            outcome.errors.append(f"web_search: {type(exc).__name__}: {str(exc)[:120]}")
            await event_log.log_event(run_id, "error", query.query_id, kind=type(exc).__name__)
            payload = None
        if payload is not None:
            payload, _ = apply_grounding(payload, outcome.trace, web_search=True)
            await event_log.log_event(
                run_id, "agent_answer", query.query_id,
                registry_id=payload.registry_id, confidence=payload.clamped_confidence(),
                reasoning=payload.reasoning, endpoint="web_search",
            )
            outcome.candidates.append(ExtractionResult.from_payload(query.query_id, payload))

    return outcome
