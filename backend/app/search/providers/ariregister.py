"""Estonia — e-Business Register (ariregister.rik.ee). Keyless, scoped EE."""

from app.integrations import ariregister
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class AriregisterSearchProvider(SearchProvider):
    name = "ariregister"
    jurisdictions = {"EE"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await ariregister.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
