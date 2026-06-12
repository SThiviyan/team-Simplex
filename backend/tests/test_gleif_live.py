"""Live test for the GLEIF provider — hits the real (keyless) GLEIF API.

Guarded by RUN_LIVE_TESTS so default `uv run pytest` stays offline/hermetic.
Run it with:
    cd backend
    RUN_LIVE_TESTS=1 uv run pytest tests/test_gleif_live.py -v -s
"""

import os

import pytest

from app.search.providers.gleif import GleifSearchProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run network-dependent GLEIF tests",
)


async def test_search_real_entities():
    results = await GleifSearchProvider().search("Nestle", limit=5)
    assert results, "expected at least one GLEIF result"
    for r in results:
        assert r.source == "gleif"
        assert r.url and r.url.startswith("https://search.gleif.org/#/record/")
        assert 0.0 <= r.score <= 1.0
        print(f"  {r.score:.2f}  {r.title}  ->  {r.url}")
