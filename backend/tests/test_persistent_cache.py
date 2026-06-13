"""Persistent on-disk search cache."""

import time


def test_persistent_cache_roundtrip_and_ttl(monkeypatch, tmp_path):
    import app.search.persistent_cache as pc

    monkeypatch.setattr(pc, "_db_path", tmp_path / "cache.db")
    monkeypatch.setattr(pc, "_conn", None)

    assert pc.get("ns", "k", max_age=100) is None
    pc.set("ns", "k", {"hello": "world", "n": 3})
    assert pc.get("ns", "k", max_age=100) == {"hello": "world", "n": 3}
    # Expired entries are misses.
    assert pc.get("ns", "k", max_age=-1) is None
    # Namespaced clear.
    pc.set("other", "x", [1, 2])
    assert pc.clear("ns") == 1
    assert pc.get("ns", "k", max_age=100) is None
    assert pc.get("other", "x", max_age=100) == [1, 2]


async def test_provider_cache_serves_second_call_without_hitting_api(monkeypatch, tmp_path):
    import app.search.persistent_cache as pc
    from app.search import search_cache
    from app.search.base import SearchProvider, SearchResult

    monkeypatch.setattr(pc, "_db_path", tmp_path / "cache.db")
    monkeypatch.setattr(pc, "_conn", None)
    search_cache.clear()

    calls = {"n": 0}

    class CountingProvider(SearchProvider):
        name = "counting"
        jurisdictions = None

        async def search(self, query, limit=10):
            calls["n"] += 1
            return [SearchResult(title="X", snippet="", score=0.9, source="counting",
                                 registry_id="ID1", register_name="X GmbH")]

    p = CountingProvider()
    r1 = await search_cache.cached_search(p, "Acme", 10)
    search_cache.clear()  # drop the in-memory layer -> force the disk layer
    r2 = await search_cache.cached_search(p, "Acme", 10)
    assert calls["n"] == 1  # second call served from disk, API not hit again
    assert r2[0].registry_id == r1[0].registry_id

    _ = time  # keep import meaningful
