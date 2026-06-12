"""Norway — Brønnøysundregistrene (Brreg) search provider. Keyless, scoped NO."""

from app.integrations import brreg
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class BrregSearchProvider(SearchProvider):
    name = "brreg"
    jurisdictions = {"NO"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await brreg.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
