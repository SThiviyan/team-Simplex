"""Japan — gBizINFO (METI) search provider. Token-gated, scoped JP.

Reads ``settings.gbizinfo_api_token``; when unset the provider disables itself
(returns []) so the keyless sources still work. Failures are non-fatal.
"""

from app.config import settings
from app.integrations import gbizinfo
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class GbizInfoSearchProvider(SearchProvider):
    name = "gbizinfo"
    jurisdictions = {"JP"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        token = settings.gbizinfo_api_token
        if not token:
            return []  # no token configured — self-disable
        try:
            rows = await gbizinfo.search_companies(query, token, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
