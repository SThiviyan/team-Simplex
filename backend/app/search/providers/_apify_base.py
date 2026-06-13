"""Shared base for Apify-actor-backed providers.

Each subclass sets ``name``, ``jurisdictions``, and ``_search`` (the integration
coroutine ``(query, token, *, limit) -> list[row dict]``). All Apify providers
are premium and opt-in: they require an Apify token AND the global APIFY_ENABLED
flag, and degrade to [] on error. The integration returns rows in the shared
shape (``number, name, legal_form, city, status, incorporation_date, last_update,
country, address, url, court, snippet, metadata``).
"""

from app.config import settings
from app.search.base import SearchProvider, SearchResult


class ApifyProvider(SearchProvider):
    cost = "premium"
    # Apify actors run ~20s cold — over the 12s default gather timeout, so raise
    # the per-provider budget or the call gets cancelled before it returns.
    search_timeout = 35.0
    # Subclasses set this to the integration MODULE (exposing an async
    # `search_companies(query, token, *, limit)`). Looked up at call time so the
    # function stays patchable.
    _integration = None

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not (settings.apify_api_key and settings.apify_enabled):
            return []
        try:
            rows = await self._integration.search_companies(
                query, settings.apify_api_key, limit=limit
            )
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
                    snippet=r.get("snippet") or "",
                    score=round(max(0.4, 0.95 - i * 0.05), 4),
                    source=self.name,
                    jurisdiction=r.get("country"),
                    registry_id=str(num) if num else None,
                    registry_court=r.get("court"),
                    register_name=name,
                    organization_type=r.get("legal_form"),
                    status=r.get("status"),
                    incorporation_date=r.get("incorporation_date"),
                    last_update=r.get("last_update"),
                    address=r.get("address"),
                    metadata=r.get("metadata") or {},
                )
            )
        return results
