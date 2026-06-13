"""Netherlands — KVK (Kamer van Koophandel) commercial register. Scoped NL.

Uses the KVK Zoeken API (settings.kvk_api_key). The default public test key
returns synthetic data from the test endpoint; a production key returns real
companies. Disables itself only if the key is explicitly unset. Failures are
non-fatal (degrade to []).
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
        if not key:
            return []
        try:
            rows = await kvk_nl.search_companies(query, key, limit=limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
