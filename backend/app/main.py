from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

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
    """Parse the query as `name,jurisdiction` CSV, include ALL matching results
    (filtered to the jurisdiction when given), and write them to a JSON file."""
    return await csv_search(app.state.providers, q, limit=limit)


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


# Serve built frontend at / (after `npm run build` populates frontend/dist).
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
