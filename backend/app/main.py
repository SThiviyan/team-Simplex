import contextlib
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.mcp_servers.country_endpoints import country_servers

# NOTE: app.pipeline.* is imported lazily inside the /api/pipeline handlers, not
# here. The pipeline pulls in `anthropic`; keeping it out of module scope means a
# missing/broken pipeline dependency can never take down the search endpoints.
from app.search.csv_search import csv_search
from app.search.orchestrator import FederatedSearch
from app.search.resolver import CompanyResolver
from app.search.sources import all_providers


# The per-country MCP endpoints (/mcp/<bucket>) need their streamable-HTTP
# session managers running. run() is one-shot per server instance, so start
# them once per process and keep them alive (lifespans re-enter under tests).
_mcp_sessions_started = False
_mcp_session_stack = contextlib.AsyncExitStack()


async def _ensure_mcp_session_managers() -> None:
    global _mcp_sessions_started
    if _mcp_sessions_started:
        return
    _mcp_sessions_started = True
    for server in country_servers().values():
        await _mcp_session_stack.enter_async_context(server.session_manager.run())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Global providers (GLEIF, Wikidata) are always relevant; national ones are
    # jurisdiction-scoped so the resolver only calls the registers that matter.
    providers = all_providers()
    app.state.providers = providers
    # /api/search stays on the fast global sources.
    app.state.search = FederatedSearch(
        providers=[p for p in providers if p.jurisdictions is None]
    )
    # /api/resolve does jurisdiction-aware gather + cross-reference.
    app.state.resolver = CompanyResolver(providers)
    await _ensure_mcp_session_managers()
    yield


app = FastAPI(title="Sinpex Hack Backend", lifespan=lifespan)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(default=10, ge=1, le=50),
):
    results = await app.state.search.search(q, limit=limit)
    return {"query": q, "count": len(results), "results": results}


@app.get("/api/csv-search")
async def csv_search_endpoint(
    q: str = Query(..., min_length=1, description="CSV text: rows of name,jurisdiction"),
    limit: int = Query(default=25, ge=1, le=50),
):
    """Pass the frontend input to the gather orchestrator, then run the matching
    layer in-memory and return the winning company per query.

    Flow: parse `name,jurisdiction` CSV -> query every relevant source (gather
    layer, also written to search_results.json) -> RapidFuzz gross filter + LLM
    semantic filter (matching layer) -> winning candidate. The gathered records
    are passed directly into the matching layer (no JSON round-trip), and the
    `winners` are returned to the frontend so it can show the final result.
    """
    import asyncio

    from app.matching.pipeline import match_payload, run_matching
    from app.search.csv_search import _to_record, search_jurisdiction

    payload = await csv_search(app.state.providers, q, limit=limit)
    # Run the matching layer directly on the in-memory gathered records (the
    # name/jurisdiction come from the same parsed input). No JSON reload.
    # Mock when there's no key (or PIPELINE_MOCK) so the chain never hard-fails.
    mock = settings.pipeline_mock or not os.environ.get("ANTHROPIC_API_KEY")
    winners = await match_payload(payload, model=settings.matching_model, mock=mock)

    # Recursion (one round, parity with the pipeline): when the matcher flags
    # "PwC" -> "PricewaterhouseCoopers", re-gather with the expanded name and
    # re-match. The winner carries `recursion` so the UI can show why.
    records_by_query: dict[str, list[dict]] = {}
    for rec in payload.get("results") or []:
        if rec.get("name_normalized_register_name"):
            records_by_query.setdefault(rec.get("query_id"), []).append(rec)

    for i, winner in enumerate(winners):
        suggested = (winner.get("recursive_search") or {}).get("suggested_query")
        if winner.get("decision") != "recursive_search" or not suggested:
            continue
        new_results, _, _ = await search_jurisdiction(
            app.state.providers, suggested, winner.get("jurisdiction"), limit
        )
        combined = records_by_query.get(winner.get("query_id"), []) + [
            _to_record(winner.get("query_id"), suggested, r) for r in new_results
        ]
        rematch = await asyncio.to_thread(
            run_matching, combined, suggested, winner.get("jurisdiction") or "",
            model=settings.matching_model, mock=mock,
        )
        winners[i] = {
            "query_id": winner.get("query_id"),
            "name": winner.get("name"),
            "jurisdiction": winner.get("jurisdiction"),
            **rematch,
            "recursion": {
                "expanded_from": winner.get("name"),
                "suggested_query": suggested,
                "cause": "abbreviation_expansion"
                if winner.get("query_kind") == "abbreviation"
                else "semantic_filter_flag",
            },
        }

    payload["winners"] = winners
    # Drop the raw gathered rows from the HTTP response; they live in the JSON
    # file (and are summarised by `winners`/`count` here).
    payload.pop("results", None)
    return payload


@app.get("/api/resolve")
async def resolve(
    q: str = Query(..., min_length=1, description="Company name to resolve"),
    jurisdiction: str | None = Query(
        default=None, description="ISO 3166-1 alpha-2 code, e.g. DE, GB, HU"
    ),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Gather across jurisdiction-relevant sources and cross-reference into a
    most-likely match."""
    return await app.state.resolver.resolve(q, jurisdiction=jurisdiction, limit=limit)


class PipelineQuery(BaseModel):
    """A single search input: search query (name) + country code (jurisdiction)."""

    query_id: str | None = None
    name: str
    jurisdiction: str


class PipelineRunRequest(BaseModel):
    query: PipelineQuery | None = Field(
        default=None, description="Run a single ad-hoc input instead of the batch CSV"
    )
    limit: int | None = Field(
        default=None, ge=1, description="Only process the first N rows of the batch CSV"
    )


def _require_pipeline_ready() -> None:
    if not settings.pipeline_mock and not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set (and PIPELINE_MOCK is off) — add it to .env",
        )


@app.post("/api/pipeline/run")
async def pipeline_run(req: PipelineRunRequest | None = None):
    """Run the registry-lookup chain: per-country MCP list -> agent -> filter -> Claude eval -> CSV."""
    from app.pipeline.models import QueryRow
    from app.pipeline.runner import run_pipeline

    _require_pipeline_ready()
    req = req or PipelineRunRequest()
    queries = None
    if req.query is not None:
        queries = [
            QueryRow(
                query_id=req.query.query_id or f"adhoc-{uuid.uuid4().hex[:8]}",
                name=req.query.name,
                jurisdiction=req.query.jurisdiction,
            )
        ]
    return await run_pipeline(queries=queries, limit=req.limit)


@app.get("/api/pipeline/runs")
async def pipeline_runs(limit: int = Query(default=20, ge=1, le=100)):
    """Recent pipeline runs + status — entry point for the live-status UI."""
    from app.pipeline import event_log

    return {"runs": event_log.list_runs(limit=limit)}


@app.get("/api/pipeline/runs/{run_id}/events")
async def pipeline_run_events(
    run_id: str,
    after: int = Query(default=0, ge=0, description="Return events with seq > after"),
    limit: int = Query(default=1000, ge=1, le=5000),
):
    """Incremental event feed for one run: every agent action with its reasoning
    (tool calls, grounding checks, eval decisions, confidence rationale). The UI
    polls with the last seen `seq` as `after` to render a live agent view."""
    from app.pipeline import event_log

    events = event_log.list_events(run_id, after=after, limit=limit)
    return {
        "run_id": run_id,
        "events": events,
        "last_seq": events[-1]["seq"] if events else after,
    }


@app.post("/api/pipeline/run-csv")
async def pipeline_run_csv(file: UploadFile) -> FileResponse:
    """Eligibility gate: upload the test CSV, get the result CSV back — no manual steps.

    Delimiter (comma/semicolon) is auto-detected; the response is the finished
    comma-delimited result CSV as a file download.
    """
    from app.pipeline.csv_io import read_queries_text
    from app.pipeline.runner import run_pipeline

    _require_pipeline_ready()
    text = (await file.read()).decode("utf-8-sig")
    queries = read_queries_text(text)
    if not queries:
        raise HTTPException(
            status_code=400,
            detail="No rows parsed — expected a CSV with columns query_id, name, jurisdiction",
        )
    summary = await run_pipeline(queries=queries)
    output = Path(summary.output_csv)
    return FileResponse(output, media_type="text/csv", filename=output.name)


# Per-country MCP endpoints: /mcp/de, /mcp/us, ..., /mcp/global. The pipeline
# agent connects to the bucket matching the query's country code; deployed,
# these are also reachable by external MCP clients.
for _bucket, _server in country_servers().items():
    app.mount(f"/mcp/{_bucket}", _server.streamable_http_app())


# Serve built frontend at / (after `npm run build` populates frontend/dist).
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
