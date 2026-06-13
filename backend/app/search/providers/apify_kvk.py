"""Netherlands — KvK Handelsregister NAME search via Apify. Opt-in, scoped NL.

Requires an Apify token AND the global APIFY_ENABLED flag; self-disables and
degrades to [] otherwise. Complements the built-in ``kvk`` provider (test data)
with real Dutch register name search.
"""

from app.config import settings
from app.integrations import apify_kvk
from app.search.base import SearchProvider, SearchResult


def _snippet(row: dict) -> str:
    md = row.get("metadata") or {}
    bits = [
        row.get("legal_form"),
        row.get("city"),
        "active" if md.get("is_active") else "inactive",
        md.get("activity"),
    ]
    return " · ".join(str(b) for b in bits if b)


class ApifyKvkSearchProvider(SearchProvider):
    name = "apify_kvk_nl"
    jurisdictions = {"NL"}
    enabled = True
    cost = "premium"
    search_timeout = 35.0  # Apify actor ~20s cold > 12s gather default

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not (settings.apify_api_key and settings.apify_enabled):
            return []
        try:
            rows = await apify_kvk.search_companies(query, settings.apify_api_key, limit=limit)
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
                    register_name=name,
                    organization_type=r.get("legal_form"),
                    status=r.get("status"),
                    address=r.get("address"),
                    metadata=r.get("metadata") or {},
                )
            )
        return results
