"""Canada (British Columbia) — OrgBook BC registry. Keyless, scoped CA."""

from app.integrations import orgbook_ca
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class OrgbookCaSearchProvider(SearchProvider):
    name = "orgbook"
    jurisdictions = {"CA"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await orgbook_ca.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
