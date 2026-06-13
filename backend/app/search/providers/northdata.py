"""NorthData (DACH + EU) name search via the Apify actor.

Opt-in: requires an Apify token (APIFY_API_KEY) AND the global ``apify_enabled``
flag (APIFY_ENABLED), because each search runs a paid Apify actor (~20s). Scoped
to DACH (NorthData's core); self-disables otherwise and degrades to [] on error.

NOTE: a NorthData run (~20s cold) is slower than the gather layer's default
``provider_timeout`` (12s) — raise PROVIDER_TIMEOUT (e.g. 30) to actually use it.
"""

from app.config import settings
from app.integrations import apify_northdata
from app.search.base import SearchProvider, SearchResult


class NorthDataSearchProvider(SearchProvider):
    name = "northdata"
    jurisdictions = {"DE", "AT", "CH"}
    enabled = True
    cost = "premium"

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not (settings.apify_api_key and settings.apify_enabled):
            return []  # opt-in: needs the token and the global APIFY_ENABLED flag
        try:
            rows = await apify_northdata.search_companies(query, settings.apify_api_key, limit=limit)
        except Exception:
            return []

        results: list[SearchResult] = []
        for i, r in enumerate(rows[:limit]):
            name = r.get("name")
            if not name:
                continue
            snippet = r.get("snippet") or " · ".join(
                str(x) for x in (r.get("city"), r.get("country")) if x
            )
            results.append(
                SearchResult(
                    title=name,
                    url=r.get("url"),
                    snippet=snippet,
                    score=round(max(0.4, 0.95 - i * 0.05), 4),
                    source=self.name,
                    jurisdiction=r.get("country"),
                    registry_id=r.get("number"),
                    registry_court=r.get("court"),
                    register_name=name,
                    address=r.get("address"),
                    metadata=r.get("metadata") or {},
                )
            )
        return results
