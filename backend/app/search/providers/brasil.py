"""Brazil — Receita Federal CNPJ via BrasilAPI search provider.

Keyless, scoped to BR. Resolves the company when the query carries a CNPJ; for a
name-only query it returns [] (no public name search), and GLEIF / the scrape
layer cover the name path. Failures are non-fatal (degrade to []).
"""

from app.integrations import brasil
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class BrasilSearchProvider(SearchProvider):
    name = "brasil_cnpj"
    jurisdictions = {"BR"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await brasil.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
