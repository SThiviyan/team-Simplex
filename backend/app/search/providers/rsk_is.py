"""Iceland — fyrirtækjaskrá (company register, skatturinn.is), scraped.
Keyless, scoped IS."""

from app.integrations import rsk_is
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class RskIsSearchProvider(SearchProvider):
    name = "rsk"
    jurisdictions = {"IS"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await rsk_is.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
