import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings

# NOTE: app.pipeline.* is imported lazily inside the /api/pipeline handlers, not
# here. The pipeline pulls in `anthropic`; keeping it out of module scope means a
# missing/broken pipeline dependency can never take down the search endpoints.
from app.search.csv_search import csv_search
from app.search.orchestrator import FederatedSearch
from app.search.resolver import CompanyResolver
from app.search.sources import all_providers


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
    from app.matching.pipeline import match_payload

    payload = await csv_search(app.state.providers, q, limit=limit)
    # Run the matching layer directly on the in-memory gathered records (the
    # name/jurisdiction come from the same parsed input). No JSON reload.
    # Mock when there's no key (or PIPELINE_MOCK) so the chain never hard-fails.
    mock = settings.pipeline_mock or not os.environ.get("ANTHROPIC_API_KEY")
    payload["winners"] = await match_payload(
        payload, model=settings.anthropic_model, mock=mock
    )
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


# Serve built frontend at / (after `npm run build` populates frontend/dist).
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
