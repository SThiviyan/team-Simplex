"""Ireland — Companies Registration Office (CRO) search provider.

Keyless: backed by the CRO open dataset on data.gov.ie. Scoped IE.
"""

from app.integrations import cro
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class CroSearchProvider(SearchProvider):
    name = "cro"
    jurisdictions = {"IE"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await cro.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
