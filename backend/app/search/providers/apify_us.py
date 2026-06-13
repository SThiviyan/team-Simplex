"""USA — business-entity NAME search across state registries via Apify.

Opt-in, scoped US. Requires an Apify token AND the global APIFY_ENABLED flag;
self-disables and degrades to [] otherwise. Complements SEC (federal) and the
pipeline's state-scrape registries with structured state-register name search.
"""

from app.config import settings
from app.integrations import apify_us
from app.search.base import SearchProvider, SearchResult


def _snippet(row: dict) -> str:
    md = row.get("metadata") or {}
    bits = [md.get("state"), row.get("legal_form"), row.get("status"), row.get("city")]
    return " · ".join(str(b) for b in bits if b)


class ApifyUsSearchProvider(SearchProvider):
    name = "apify_us"
    jurisdictions = {"US"}
    enabled = True
    cost = "premium"
    search_timeout = 35.0  # Apify actor ~20s cold > 12s gather default

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not (settings.apify_api_key and settings.apify_enabled):
            return []
        try:
            rows = await apify_us.search_companies(query, settings.apify_api_key, limit=limit)
        except Exception:
            return []

        results: list[SearchResult] = []
        for i, r in enumerate(rows[:limit]):
            name = r.get("name")
            if not name:
                continue
            num = r.get("number")
            results.append(
                SearchResult(
                    title=f"{name} ({num})" if num else name,
                    url=r.get("url"),
                    snippet=_snippet(r),
                    score=round(max(0.4, 0.95 - i * 0.05), 4),
                    source=self.name,
                    jurisdiction=r.get("country"),
                    registry_id=str(num) if num else None,
                    registry_court=r.get("court"),
                    register_name=name,
                    organization_type=r.get("legal_form"),
                    status=r.get("status"),
                    incorporation_date=r.get("incorporation_date"),
                    address=r.get("address"),
                    metadata=r.get("metadata") or {},
                )
            )
        return results
