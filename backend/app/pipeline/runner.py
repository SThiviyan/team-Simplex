"""Pipeline orchestration — the chain of instructions behind POST /api/pipeline/run.

Per query row: country code -> ranked MCP list -> Layer-1 agent (top-to-bottom loop)
-> Python prefilter -> Claude eval -> final row. All rows are then written back to CSV.

Rows run concurrently (bounded by settings.pipeline_concurrency); output order always
matches input order, and a crash in one row never takes down the batch — the judges'
test CSV must run end-to-end no matter what.
"""

import asyncio
import logging
from pathlib import Path

from app.config import settings
from app.pipeline import csv_io
from app.pipeline.agent import run_layer1
from app.pipeline.evaluator import evaluate
from app.pipeline.filtering import prefilter
from app.pipeline.mcp_registry import get_mcp_servers
from app.pipeline.models import ExtractionResult, PipelineRunSummary, QueryRow

logger = logging.getLogger(__name__)


async def process_query(query: QueryRow) -> ExtractionResult:
    mcps = get_mcp_servers(query.jurisdiction)
    candidates = await run_layer1(query, mcps)
    filtered = prefilter(candidates)
    return await evaluate(query, filtered)


async def _process_guarded(query: QueryRow, semaphore: asyncio.Semaphore) -> ExtractionResult:
    async with semaphore:
        logger.info("processing %s (%s, %s)", query.query_id, query.name, query.jurisdiction)
        try:
            return await process_query(query)
        except Exception as exc:
            # One bad row must not kill the batch — emit an honest error row instead.
            logger.exception("pipeline failed for query %s", query.query_id)
            return ExtractionResult(
                query_id=query.query_id,
                confidence=0.0,
                no_match_reason=f"pipeline_error: {type(exc).__name__}: {str(exc)[:120]}",
            )


async def run_pipeline(
    queries: list[QueryRow] | None = None,
    limit: int | None = None,
    output_dir: Path = csv_io.OUTPUT_DIR,
) -> PipelineRunSummary:
    if queries is None:
        queries = csv_io.read_queries()
    if limit is not None:
        queries = queries[:limit]

    semaphore = asyncio.Semaphore(settings.pipeline_concurrency)
    # gather() preserves input order regardless of completion order.
    results = list(await asyncio.gather(*(_process_guarded(q, semaphore) for q in queries)))

    output_path = csv_io.write_results(results, output_dir=output_dir)
    return PipelineRunSummary(
        rows_processed=len(results),
        output_csv=str(output_path),
        results=results,
    )
