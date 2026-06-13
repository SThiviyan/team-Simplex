"""Pipeline orchestration — the chain behind POST /api/pipeline/run.

Per query row: country code -> ranked MCP endpoint list -> Layer-1 Claude agent
(MCP tool loop + grounding) -> RapidFuzz gross filter + LLM semantic filter
(colleague's matching layer, run conditionally) -> recursion on the matcher's
flag -> final row. All rows are then written back to CSV.

Rows run concurrently (bounded by settings.pipeline_concurrency); output order
always matches input order, and a crash in one row never takes down the batch.
Every step emits an event into the agent event log (the live UI feed).
"""

import asyncio
import logging
import os
from pathlib import Path

from app.config import settings
from app.pipeline import csv_io, event_log
from app.pipeline.agent import run_layer1
from app.pipeline.enrichment import enrich_result, names_agree
from app.pipeline.mcp_registry import get_mcp_servers
from app.pipeline.models import ExtractionResult, PipelineRunSummary, QueryRow

logger = logging.getLogger(__name__)

MAX_RECURSION_ROUNDS = 1


def _clamp(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _result_from_match(query: QueryRow, match: dict) -> ExtractionResult:
    """Map a matching-layer outcome to the final output row."""
    winner = match.get("winning_candidate")
    if match.get("decision") == "match" and winner:
        return ExtractionResult(
            query_id=query.query_id,
            registry_id=winner.get("registry_id"),
            registry_court=winner.get("registry_court"),
            name_normalized_register_name=winner.get("name_normalized_register_name"),
            jurisdiction_confirmed=winner.get("jurisdiction_confirmed"),
            confidence=_clamp(match.get("confidence")),
            source=winner.get("source"),
            # The output contract: a blank registry_id always says why. The
            # entity was identified, but no source exposed its register number.
            no_match_reason=None if winner.get("registry_id") else "id_not_in_sources",
        )
    # "no_match_in_sources" ≠ "not_in_registry": the first says OUR sources had
    # nothing usable; only the agent (with evidence) may claim the second.
    label = "ambiguous_candidates" if match.get("candidates") else "no_match_in_sources"
    return ExtractionResult(
        query_id=query.query_id, confidence=_clamp(match.get("confidence")), no_match_reason=label
    )


async def _run_matching(query: QueryRow, records: list[dict], run_id: str) -> dict:
    from app.matching.pipeline import run_matching  # heavy import kept lazy

    mock = settings.pipeline_mock or not os.environ.get("ANTHROPIC_API_KEY")
    await event_log.log_event(
        run_id, "eval_started", query.query_id, record_count=len(records), mock=mock
    )
    match = await asyncio.to_thread(
        run_matching,
        records,
        query.name,
        query.jurisdiction or "",
        model=settings.matching_model,
        mock=mock,
    )
    await event_log.log_event(
        run_id, "eval_result", query.query_id,
        decision=match.get("decision"), confidence=match.get("confidence"),
        reasoning=match.get("reasoning"),
        query_kind=match.get("query_kind"),
        kept_candidates=len(match.get("candidates") or []),
    )
    # gross_filter already carries records_in/candidates_kept — merge, don't duplicate.
    filter_stats = {
        "records_in": len(records),
        "candidates_kept": len(match.get("candidates") or []),
        **(match.get("gross_filter") or {}),
    }
    await event_log.log_event(run_id, "filter_result", query.query_id, **filter_stats)
    return match


def _result_cache_key(query: QueryRow) -> str:
    from app.search.base import normalize_country

    cc = normalize_country((query.jurisdiction or "").split("-")[0]) or (query.jurisdiction or "")
    return f"{query.name.strip().lower()}|{cc.upper()}"


async def process_query(query: QueryRow, run_id: str) -> ExtractionResult:
    """Layer 1 decides WHICH entity; Layer 2 enriches it (Tier A/B) and stamps
    the calibration verdict (confidence_flag + caps). Every row goes through
    enrichment so blanks are marked and confidence is consistent."""
    # Optional full-result replay cache (testing): return a stored answer for an
    # identical (name, jurisdiction) with no API/LLM calls.
    use_cache = settings.pipeline_result_cache and not settings.pipeline_mock
    if use_cache:
        from app.search import persistent_cache

        cached = persistent_cache.get(
            "result", _result_cache_key(query), max_age=settings.search_cache_ttl_seconds
        )
        if cached is not None:
            await event_log.log_event(run_id, "result_cache_hit", query.query_id)
            return ExtractionResult(**{**cached, "query_id": query.query_id})

    row, records = await _decide(query, run_id)
    result = await enrich_result(query, row, records, run_id)

    if use_cache:
        from app.search import persistent_cache

        persistent_cache.set("result", _result_cache_key(query), result.model_dump())
    return result


async def _decide(query: QueryRow, run_id: str) -> tuple[ExtractionResult, list[dict]]:
    mcps = get_mcp_servers(query.jurisdiction)
    outcome = await run_layer1(query, mcps, run_id)

    error_row = outcome.error_result(query.query_id)
    if error_row is not None:
        # The agent call died (e.g. a 529 storm outlasting all retries) — but
        # the tool results it gathered first are still valid evidence. Salvage
        # them through the matching layer instead of discarding the row.
        if not outcome.records:
            return error_row, []
        await event_log.log_event(
            run_id, "records_salvaged", query.query_id,
            record_count=len(outcome.records), errors=outcome.errors[:3],
        )

    best = max(outcome.candidates, key=lambda c: c.confidence, default=None)

    # Conditional eval: a single grounded high-confidence agent answer needs no
    # second Claude call (token/529 mitigation). Ambiguity always gets evaluated.
    single_clear_answer = (
        best is not None
        and len(outcome.candidates) == 1
        and (best.registry_id or best.no_match_reason)
        and best.confidence >= settings.confidence_threshold
    )
    if single_clear_answer or not outcome.records:
        if best is None:
            return ExtractionResult(
                query_id=query.query_id, confidence=0.0, no_match_reason="no_match_in_sources"
            ), outcome.records
        await event_log.log_event(
            run_id, "eval_skipped", query.query_id,
            reason="single grounded high-confidence candidate"
            if single_clear_answer
            else "no gathered records to re-evaluate",
            confidence=best.confidence,
        )
        return best, outcome.records

    # Matching layer: RapidFuzz gross filter + LLM semantic filter over the
    # records the agent's MCP tool calls returned.
    records = outcome.records
    match = await _run_matching(query, records, run_id)

    # The matcher can flag a recursive search ("BMW" -> "Bayerische Motoren
    # Werke AG"): re-enter Layer 1 with the expanded name — the diagram's arrow.
    rounds = 0
    while match.get("decision") == "recursive_search" and rounds < MAX_RECURSION_ROUNDS:
        rounds += 1
        suggested = (match.get("recursive_search") or {}).get("suggested_query")
        if not suggested:
            break
        await event_log.log_event(
            run_id, "recursion_triggered", query.query_id,
            suggested_query=suggested,
            expanded_from=query.name,
            # Why the recursion started — for the UI: "abbreviation_expansion"
            # means the query was abbreviation-shaped (e.g. "PwC") and the
            # semantic filter suggested the expanded legal name.
            cause="abbreviation_expansion"
            if match.get("query_kind") == "abbreviation"
            else "semantic_filter_flag",
        )
        requery = QueryRow(
            query_id=query.query_id, name=suggested, jurisdiction=query.jurisdiction
        )
        re_outcome = await run_layer1(requery, mcps, run_id)
        records = records + re_outcome.records
        match = await _run_matching(requery, records, run_id)

    final = _result_from_match(query, match)

    # Don't let the matcher bury a grounded agent answer. Two cases:
    # - matcher confirmed the SAME entity but its winner carries no registry
    #   number (e.g. a Wikidata record): the agent's grounded ID row is
    #   strictly more complete — confidence comparison doesn't apply;
    # - matcher couldn't corroborate at all: prefer the stronger of the two.
    if final.registry_id is None and best is not None and best.registry_id:
        same_entity_no_id = match.get("decision") == "match" and names_agree(
            final.name_normalized_register_name, best.name_normalized_register_name
        )
        if same_entity_no_id or best.confidence > final.confidence:
            await event_log.log_event(
                run_id, "agent_override", query.query_id,
                agent_confidence=best.confidence, matcher_confidence=final.confidence,
                reason="matcher winner lacks registry_id for the same entity"
                if same_entity_no_id else "agent answer stronger than non-match",
            )
            return best, records

    return final, records


async def _process_guarded(
    query: QueryRow, semaphore: asyncio.Semaphore, run_id: str
) -> ExtractionResult:
    async with semaphore:
        logger.info("processing %s (%s, %s)", query.query_id, query.name, query.jurisdiction)
        await event_log.log_event(
            run_id, "query_started", query.query_id,
            name=query.name, jurisdiction=query.jurisdiction,
        )
        try:
            result = await process_query(query, run_id)
        except Exception as exc:
            # One bad row must not kill the batch — emit an honest error row instead.
            logger.exception("pipeline failed for query %s", query.query_id)
            await event_log.log_event(
                run_id, "error", query.query_id, kind=type(exc).__name__, message=str(exc)[:200]
            )
            result = ExtractionResult(
                query_id=query.query_id,
                confidence=0.0,
                no_match_reason=f"pipeline_error: {type(exc).__name__}: {str(exc)[:120]}",
            )
        await event_log.log_event(
            run_id, "query_completed", query.query_id,
            registry_id=result.registry_id, confidence=result.confidence,
            no_match_reason=result.no_match_reason,
        )
        return result


async def run_pipeline(
    queries: list[QueryRow] | None = None,
    limit: int | None = None,
    output_dir: Path = csv_io.OUTPUT_DIR,
) -> PipelineRunSummary:
    if queries is None:
        queries = csv_io.read_queries()
    if limit is not None:
        queries = queries[:limit]

    run_id = event_log.new_run_id()
    await event_log.log_event(run_id, "run_started", rows=len(queries))

    semaphore = asyncio.Semaphore(settings.pipeline_concurrency)
    # gather() preserves input order regardless of completion order.
    results = list(
        await asyncio.gather(*(_process_guarded(q, semaphore, run_id) for q in queries))
    )

    output_path = csv_io.write_results(results, output_dir=output_dir)
    await event_log.log_event(run_id, "run_completed", rows=len(results), output_csv=str(output_path))
    return PipelineRunSummary(
        run_id=run_id,
        rows_processed=len(results),
        output_csv=str(output_path),
        results=results,
    )
