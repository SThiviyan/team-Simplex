"""Poland — KRS (Krajowy Rejestr Sądowy) search provider. Keyless, scoped PL.

Resolves the company when the query carries a KRS number; a name-only query
returns [] (no public name search) and GLEIF / the scrape layer cover names.
Failures are non-fatal (degrade to []).
"""

from app.integrations import krs_pl
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class KrsPlSearchProvider(SearchProvider):
    name = "krs_pl"
    jurisdictions = {"PL"}
    enabled = True
    lookup = "number"

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await krs_pl.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
