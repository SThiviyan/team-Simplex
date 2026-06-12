"""Denmark — CVR (via cvrapi.dk) search provider. Keyless, scoped DK."""

from app.integrations import cvr
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class CvrSearchProvider(SearchProvider):
    name = "cvr"
    jurisdictions = {"DK"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await cvr.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
