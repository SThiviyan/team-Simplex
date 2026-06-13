"""Switzerland — Zefix commercial register via the LINDAS SPARQL portal. Keyless,
scoped CH. Carries UID, legal name, legal form, address and business purpose, so
it's a foundation source (supplies the registry_id), not just a supplement."""

from app.integrations import lindas
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class LindasSearchProvider(SearchProvider):
    name = "lindas"
    jurisdictions = {"CH"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await lindas.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
