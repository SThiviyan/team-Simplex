"""Layer 1 — DB search agent.

Walks the country's ranked MCP server list top to bottom. Each attempt is one
Claude call wired to exactly one MCP server (so the ranking is honored); when
no usable MCP is configured for the country, the agent falls back to a single
web-search call. Structured JSON output is enforced via output_config.
"""

import logging

import anthropic

from app.config import settings
from app.pipeline.models import ExtractionPayload, ExtractionResult, McpServerEntry, QueryRow

logger = logging.getLogger(__name__)

MAX_PAUSE_TURN_CONTINUATIONS = 5

# no_match_reason prefix marking "the pipeline errored", as opposed to a genuine
# registry no-match (not_in_registry / ambiguous_candidates / out_of_scope).
LAYER1_ERROR_PREFIX = "layer1_error"

_OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": ExtractionPayload.model_json_schema(),
}

_SYSTEM = """You are a company-registry research agent in a KYC pipeline.

Given a company name and a country code, find the company's official commercial-register
entry and extract the requested fields. Use the tools available to you to look the company
up; do not answer from memory alone. Prefer official registries (Handelsregister,
Companies House, national registers) over secondary sources.

Rules:
- registry_id: the official registration number from the relevant registry, exactly as registered.
- registry_court: the specific court or registry office (required for DE, AT, etc. — e.g.
  'Amtsgericht München').
- name_normalized_register_name: the FULL legal name as registered (e.g. 'Sinpex GmbH',
  not 'Sinpex').
- jurisdiction_confirmed: the confirmed country or state/province, only if the registry
  evidence confirms it; else null.
- confidence: a number in [0, 1] reflecting how sure you are. It is used for calibration
  scoring — be honest. Only go above 0.8 when registry_id and the registered name both
  clearly match the queried company.
- source: at least one citable URL or registry document reference supporting the answer.
- If you cannot find a confident match, set registry_id and the other data fields to null
  and set no_match_reason to one of: 'not_in_registry' (the company is not in this
  registry), 'ambiguous_candidates' (multiple plausible entries, cannot decide),
  'out_of_scope' (the query is not a registrable company / not answerable here), or
  another short snake_case label if none of these fit.
- Respond with the JSON object only."""


def _user_prompt(query: QueryRow, via: str) -> str:
    return (
        f"Company name (search query): {query.name}\n"
        f"Country code (jurisdiction): {query.jurisdiction}\n"
        f"Look this company up via: {via}"
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
    )


def _extract_payload(response) -> ExtractionPayload:
    text = next(b.text for b in reversed(response.content) if b.type == "text")
    return ExtractionPayload.model_validate_json(text)


async def _attempt(
    client: anthropic.AsyncAnthropic,
    query: QueryRow,
    *,
    mcp: McpServerEntry | None,
) -> ExtractionPayload | None:
    """One extraction attempt — against a single MCP server, or web search if mcp is None."""
    via = f"the MCP server '{mcp.name}'" if mcp else "web search"
    messages = [{"role": "user", "content": _user_prompt(query, via)}]

    for _ in range(MAX_PAUSE_TURN_CONTINUATIONS):
        if mcp:
            response = await client.beta.messages.create(
                model=settings.anthropic_model,
                max_tokens=16000,
                system=_SYSTEM,
                messages=messages,
                mcp_servers=[{"type": "url", "name": mcp.name, "url": mcp.url}],
                output_config={"format": _OUTPUT_FORMAT},
                betas=["mcp-client-2025-11-20"],
            )
        else:
            response = await client.messages.create(
                model=settings.anthropic_model,
                max_tokens=16000,
                system=_SYSTEM,
                messages=messages,
                tools=[{"type": "web_search_20260209", "name": "web_search"}],
                output_config={"format": _OUTPUT_FORMAT},
            )

        if response.stop_reason == "pause_turn":
            # Server-side tool loop paused — append the assistant turn and resume.
            messages = messages[:1] + [{"role": "assistant", "content": response.content}]
            continue
        if response.stop_reason == "refusal":
            logger.warning("attempt via %s refused for query %s", via, query.query_id)
            return None
        return _extract_payload(response)

    logger.warning("attempt via %s exceeded pause_turn budget for query %s", via, query.query_id)
    return None


async def run_layer1(query: QueryRow, mcps: list[McpServerEntry]) -> list[ExtractionResult]:
    """Walk the ranked MCP list top to bottom; collect candidate extractions."""
    if settings.pipeline_mock:
        return [ExtractionResult.from_payload(query.query_id, _mock_payload(query))]

    client = anthropic.AsyncAnthropic()
    usable_mcps = [m for m in mcps if not m.is_placeholder]
    results: list[ExtractionResult] = []
    errors: list[str] = []

    attempts: list[McpServerEntry | None] = usable_mcps or [None]  # None = web-search fallback
    for mcp in attempts:
        via = f"mcp:{mcp.name}" if mcp else "web_search"
        try:
            payload = await _attempt(client, query, mcp=mcp)
        except Exception as exc:
            # An MCP/tool failure must not kill the run — move down the list,
            # same philosophy as FederatedSearch's non-fatal provider errors.
            logger.exception("attempt failed for query %s (via %s)", query.query_id, via)
            errors.append(f"{via}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if payload is None:
            continue

        result = ExtractionResult.from_payload(query.query_id, payload)
        results.append(result)

        if result.confidence >= settings.confidence_threshold:
            break  # early exit — skip lower-ranked MCPs

        # TODO (architecture: 'Recursive' arrow): re-enter the agent here with a
        # refined query (e.g. normalized name from the low-confidence result).

    # TODO (architecture: Layer 2 - detailed query): once Layer 1 has confirmed the
    # entity, kick off the detailed-query layer here.

    if not results and errors:
        # Every attempt died on an exception (API overload, auth, network, ...).
        # Surface that instead of letting it masquerade as "no match found".
        return [
            ExtractionResult(
                query_id=query.query_id,
                confidence=0.0,
                no_match_reason=f"{LAYER1_ERROR_PREFIX}: {'; '.join(errors)}",
            )
        ]

    return results
