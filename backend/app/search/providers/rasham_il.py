"""Israel — Companies Registrar open data (data.gov.il). Keyless, scoped IL."""

from app.integrations import rasham_il
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class RashamIlSearchProvider(SearchProvider):
    name = "rasham"
    jurisdictions = {"IL"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await rasham_il.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
