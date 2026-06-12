"""Czech Republic — ARES (business register) search provider. Keyless, scoped CZ."""

from app.integrations import ares
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class AresSearchProvider(SearchProvider):
    name = "ares"
    jurisdictions = {"CZ"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await ares.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
