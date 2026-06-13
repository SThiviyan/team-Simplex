"""Handelsregister (German commercial register) search provider.

Primary source is the handelsregister.ai API (settings.handelsregister_api_key,
sent as x-api-key); it falls back to scraping handelsregister.de when no key is
configured or the API is not working. Jurisdiction-scoped to DE; failures are
non-fatal (degrade to []).
"""

from app.config import settings
from app.integrations import handelsregister
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class HandelsregisterSearchProvider(SearchProvider):
    name = "handelsregister"
    jurisdictions = {"DE"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await handelsregister.search_companies(
                query, settings.handelsregister_api_key, limit=limit
            )
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
