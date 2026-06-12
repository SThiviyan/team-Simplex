import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.pipeline.csv_io import read_queries_text
from app.pipeline.models import PipelineRunSummary, QueryRow
from app.pipeline.runner import run_pipeline
from app.search.orchestrator import FederatedSearch
from app.search.providers.stub import StubSearchProvider


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Wire providers here. Teams: replace stubs with real providers
    # (or rip out FederatedSearch entirely if you want a different architecture).
    app.state.search = FederatedSearch(
        providers=[
            StubSearchProvider(name="stub-a"),
            StubSearchProvider(name="stub-b"),
        ]
    )
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
async def pipeline_run(req: PipelineRunRequest | None = None) -> PipelineRunSummary:
    """Run the registry-lookup chain: per-country MCP list -> agent -> filter -> Claude eval -> CSV."""
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
