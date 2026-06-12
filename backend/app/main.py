from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

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


# Serve built frontend at / (after `npm run build` populates frontend/dist).
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="static")
