"""Finland — PRH / YTJ business register search provider. Keyless, scoped FI."""

from app.integrations import prh
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class PrhSearchProvider(SearchProvider):
    name = "prh"
    jurisdictions = {"FI"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await prh.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
