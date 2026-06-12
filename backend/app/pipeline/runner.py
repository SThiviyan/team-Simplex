"""Pipeline orchestration — the chain of instructions behind POST /api/pipeline/run.

Per query row: country code -> ranked MCP list -> Layer-1 agent (top-to-bottom loop)
-> Python prefilter -> Claude eval -> final row. All rows are then written back to CSV.
"""

import logging
from pathlib import Path

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


async def run_pipeline(
    queries: list[QueryRow] | None = None,
    limit: int | None = None,
    output_dir: Path = csv_io.OUTPUT_DIR,
) -> PipelineRunSummary:
    if queries is None:
        queries = csv_io.read_queries()
    if limit is not None:
        queries = queries[:limit]

    # TODO: parallelize with asyncio.gather (+ semaphore) once volumes grow.
    # TODO: for large batches move this behind a background task / job queue —
    # Cloud Run's request timeout (60s) won't fit a long synchronous run.
    results: list[ExtractionResult] = []
    for query in queries:
        logger.info("processing %s (%s, %s)", query.query_id, query.name, query.jurisdiction)
        results.append(await process_query(query))

    output_path = csv_io.write_results(results, output_dir=output_dir)
    return PipelineRunSummary(
        rows_processed=len(results),
        output_csv=str(output_path),
        results=results,
    )
