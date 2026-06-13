"""Netherlands — KVK (Kamer van Koophandel) commercial register. Scoped NL.

Uses the KVK Zoeken API (settings.kvk_api_key). A PRODUCTION key (developers.kvk.nl)
returns real companies. The public TEST key only reaches the test endpoint, which
returns *synthetic* data — and is currently 404ing outright — so it is treated as
"no real source": the provider self-disables on the test key rather than risk a
synthetic record becoming a registry answer. Real NL coverage then comes from the
Apify KVK actor (apify_kvk). Failures are non-fatal (degrade to []).
"""

from app.config import settings
from app.integrations import kvk_nl
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class KvkNlSearchProvider(SearchProvider):
    name = "kvk"
    jurisdictions = {"NL"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        key = settings.kvk_api_key
        # Disabled without a key, AND on the public test key (synthetic/404 — not a
        # real register). Only a production key produces citable Dutch companies.
        if not key or key == kvk_nl.TEST_KEY:
            return []
        try:
            rows = await kvk_nl.search_companies(query, key, limit=limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
