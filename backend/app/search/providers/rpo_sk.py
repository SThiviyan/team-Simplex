"""Slovakia — RPO Register of Legal Entities (api.statistics.sk). Keyless, scoped SK."""

from app.integrations import rpo_sk
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class RpoSkSearchProvider(SearchProvider):
    name = "rpo"
    jurisdictions = {"SK"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await rpo_sk.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
