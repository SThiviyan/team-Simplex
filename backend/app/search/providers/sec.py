"""United States — SEC EDGAR search provider. Keyless, scoped US (public cos)."""

from app.integrations import sec
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class SecSearchProvider(SearchProvider):
    name = "sec"
    jurisdictions = {"US"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await sec.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
