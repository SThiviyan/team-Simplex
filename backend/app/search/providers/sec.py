"""United States — SEC EDGAR search provider. Keyless, scoped US (public cos)."""

from app.integrations import sec
from app.search.base import SearchProvider, SearchResult
from app.search.providers._register import rows_to_results


class SecSearchProvider(SearchProvider):
    name = "sec"
    jurisdictions = {"US"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await sec.search_companies(query, limit)
        except Exception:
            return []
        results = rows_to_results(rows, self.name, limit)
        # A CIK is an SEC filing index key, not a company registration number —
        # US companies register at state level (Secretary of State). Surface the
        # CIK as metadata/evidence, never as registry_id.
        for r in results:
            if r.registry_id:
                r.metadata = {**r.metadata, "cik": r.registry_id}
                r.registry_id = None
        return results
