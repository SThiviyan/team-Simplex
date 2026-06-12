"""Live test for the Wikidata provider — hits the real (keyless) SPARQL endpoint.

Guarded by RUN_LIVE_TESTS so default `uv run pytest` stays offline/hermetic.
Run with:
    cd backend
    RUN_LIVE_TESTS=1 uv run pytest tests/test_wikidata_live.py -v -s
"""

import os

import pytest

from app.search.providers.wikidata import WikidataSearchProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run network-dependent Wikidata tests",
)


async def test_search_real_entities():
    results = await WikidataSearchProvider().search("Nestle", limit=5)
    assert results, "expected at least one Wikidata result"
    for r in results:
        assert r.source == "wikidata"
        assert r.url and r.url.startswith("https://www.wikidata.org/wiki/Q")
        assert 0.0 <= r.score <= 1.0
        print(f"  {r.score:.2f}  {r.title}  ->  {r.url}")
