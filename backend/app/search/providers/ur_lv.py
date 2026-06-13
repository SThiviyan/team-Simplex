"""Latvia — Register of Enterprises (data.gov.lv). Keyless, scoped LV."""

from app.integrations import ur_lv
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class UrLvSearchProvider(SearchProvider):
    name = "ur_lv"
    jurisdictions = {"LV"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await ur_lv.search_companies(query, limit)
        except Exception:
            return []
        return rows_to_results(rows, self.name, limit)
