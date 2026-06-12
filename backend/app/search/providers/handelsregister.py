"""Handelsregister (German commercial register) search provider.

Wraps the scraping client. Jurisdiction-scoped to DE, so the resolver only
calls it when the requested jurisdiction is Germany. Keyless but slow/fragile;
the synchronous scrape runs off the event loop and degrades to [] on failure.
"""

import asyncio

from app.integrations import handelsregister
from app.search.base import SearchProvider, SearchResult


class HandelsregisterSearchProvider(SearchProvider):
    name = "handelsregister"
    jurisdictions = {"DE"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            rows = await asyncio.to_thread(handelsregister.search_companies, query, limit)
        except Exception:
            return []

        results: list[SearchResult] = []
        for i, r in enumerate(rows[:limit]):
            reg = r.get("register_number")
            snippet = " · ".join(
                x for x in [r.get("court"), r.get("status"), "DE"] if x
            )
            results.append(
                SearchResult(
                    title=f"{r['name']} ({reg})" if reg else r["name"],
                    url=None,
                    snippet=snippet,
                    score=round(max(0.4, 0.95 - i * 0.04), 4),
                    source=self.name,
                    jurisdiction="DE",  # this register is German by definition
                    registry_id=reg,
                    registry_court=r.get("court"),
                    register_name=r["name"],
                    status=r.get("status"),
                )
            )
        return results
