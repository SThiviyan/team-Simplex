"""The "LLM/Claude eval" box — judges filtered candidates and produces the final row."""

import anthropic

from app.config import settings
from app.pipeline.agent import LAYER1_ERROR_PREFIX
from app.pipeline.models import ExtractionPayload, ExtractionResult, QueryRow

_OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": ExtractionPayload.model_json_schema(),
}

_SYSTEM = """You are the final evaluator in a company-registry KYC pipeline.

You receive the original search input (company name + country code) and candidate registry
extractions produced by an upstream search agent. Decide which candidate (if any) is the
correct registry entry and return ONE final JSON object in the given schema:

- Merge/correct fields where candidates disagree; name_normalized_register_name must be
  the FULL legal name as registered (e.g. 'Sinpex GmbH', not 'Sinpex').
- registry_court: the specific court or registry office (required for DE, AT, etc.).
- jurisdiction_confirmed: the confirmed country or state/province, only if the evidence
  confirms it; else null.
- confidence: your own final judgement, a number in [0, 1] — not a copy of the candidates'
  values. It is used for calibration scoring, so be honest.
- source: at least one citable URL or registry document reference supporting the answer.
- If no candidate is a credible match, set registry_id and the other data fields to null
  and set no_match_reason to one of: 'not_in_registry', 'ambiguous_candidates',
  'out_of_scope', or another short snake_case label if none of these fit.
- Respond with the JSON object only."""


def _no_match(query: QueryRow, reason: str) -> ExtractionResult:
    return ExtractionResult(query_id=query.query_id, confidence=0.0, no_match_reason=reason)


async def evaluate(query: QueryRow, candidates: list[ExtractionResult]) -> ExtractionResult:
    if not candidates:
        return _no_match(query, "no candidates produced by the Layer-1 agent")

    if all(
        c.no_match_reason and c.no_match_reason.startswith(LAYER1_ERROR_PREFIX)
        for c in candidates
    ):
        # Infra failure, not a registry verdict — pass it through unchanged
        # rather than letting the eval model re-judge (and obscure) it.
        return candidates[0]

    if settings.pipeline_mock:
        return candidates[0]  # prefilter already put the best candidate first

    client = anthropic.AsyncAnthropic()
    candidates_json = "\n".join(c.model_dump_json() for c in candidates)
    response = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Search input: name={query.name!r}, country code={query.jurisdiction}\n"
                    f"Candidates (one JSON object per line):\n{candidates_json}"
                ),
            }
        ],
        output_config={"format": _OUTPUT_FORMAT},
    )

    if response.stop_reason == "refusal":
        return _no_match(query, "evaluation refused")

    text = next(b.text for b in reversed(response.content) if b.type == "text")
    payload = ExtractionPayload.model_validate_json(text)
    return ExtractionResult.from_payload(query.query_id, payload)
