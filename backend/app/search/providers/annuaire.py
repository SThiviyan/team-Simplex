"""France — Annuaire des Entreprises search provider. Keyless, scoped FR."""

from app.integrations import annuaire
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class AnnuaireSearchProvider(SearchProvider):
    name = "annuaire"
    jurisdictions = {"FR"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await annuaire.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
