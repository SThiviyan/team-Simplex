from fastapi.testclient import TestClient

from app.main import app
from app.search.orchestrator import FederatedSearch
from app.search.providers.stub import StubSearchProvider


def test_health():
    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_search_returns_results():
    with TestClient(app) as client:
        # Keep this unit test hermetic: swap in a deterministic stub instead of
        # the live GLEIF provider. Live behaviour is covered by test_gleif_live.
        app.state.search = FederatedSearch(providers=[StubSearchProvider(name="stub")])
        r = client.get("/api/search", params={"q": "hello", "limit": 5})
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "hello"
        assert body["count"] > 0
        assert all("title" in item for item in body["results"])
