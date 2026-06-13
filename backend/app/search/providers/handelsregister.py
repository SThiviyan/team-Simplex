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
    # The JSF scrape is multi-page so it needs more than the fast-API default,
    # but NOT 45s: handelsregister.de blocks datacenter IPs and then just hangs,
    # which used to stall the whole row. Cap it so an unreachable/slow portal is
    # abandoned in a few seconds and the row escalates (GLEIF/Impressum/web).
    search_timeout = 12.0

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        # The handelsregister.de scrape is the slowest source in the stack
        # (session-based JSF, serialized, ~tens of seconds). It's opt-in: with it
        # off, GLEIF + the Impressum enrichment still cover DE registry numbers.
        from app.config import settings

        if not settings.handelsregister_scrape_fallback:
            return []
        try:
            rows = await asyncio.to_thread(handelsregister.search_companies, query, limit)
        except Exception:
            return []

        results: list[SearchResult] = []
        for i, r in enumerate(rows[:limit]):
            reg = r.get("register_number")
            court = r.get("court")
            # The portal has no per-record permalink (session-based JSF), so the
            # citation is the official registry document reference — court +
            # register number retrieves exactly this entry on the portal.
            doc_ref = f"{court} {reg}" if court and reg else None
            snippet = " · ".join(
                x for x in [doc_ref or court, r.get("status"), "DE"] if x
            )
            results.append(
                SearchResult(
                    title=f"{r['name']} ({reg})" if reg else r["name"],
                    url=handelsregister.START_URL,
                    snippet=snippet,
                    score=round(max(0.4, 0.95 - i * 0.04), 4),
                    source=self.name,
                    jurisdiction="DE",  # this register is German by definition
                    registry_id=reg,
                    registry_court=court,
                    register_name=r["name"],
                    status=r.get("status"),
                )
            )
        return results
