import pytest


@pytest.fixture(autouse=True)
def isolated_persistent_cache(tmp_path, monkeypatch):
    """Point the on-disk search cache at a per-test temp DB so cached API
    results never leak between tests (or into the real data/ cache)."""
    import app.search.persistent_cache as pc

    monkeypatch.setattr(pc, "_db_path", tmp_path / "search_cache.db")
    monkeypatch.setattr(pc, "_conn", None)
    yield
    if pc._conn is not None:
        pc._conn.close()
        pc._conn = None
