"""Shared, timeout-bounded provider gather primitives.

Both the deterministic resolver (live UI path) and the CSV/MCP search path need
the same guarantee: a slow or blocked source can never stall the caller. These
primitives enforce it in one place so the two paths can't drift apart (the UI
path previously used a bare ``asyncio.gather`` with no deadline and hung on the
slow sources).
"""

import asyncio

from app.search.base import SearchProvider, SearchResult
from app.search.search_cache import cached_search


async def bounded_search(p: SearchProvider, name: str, limit: int) -> list[SearchResult]:
    """One provider's search under a hard per-provider timeout. The cap is the
    provider's own ``search_timeout`` when set (slow scrapers/actors need more
    than the fast-API default), else ``settings.provider_timeout``. Timeouts and
    errors propagate as exceptions for the caller to drop — they are non-fatal."""
    from app.config import settings

    timeout = p.search_timeout or settings.provider_timeout
    return await asyncio.wait_for(cached_search(p, name, limit), timeout=timeout)


async def run_tier(
    providers: list[SearchProvider], name: str, limit: int, deadline: float
) -> list[SearchResult]:
    """Run one tier of providers concurrently, capped by an overall ``deadline``:
    after it, use whatever responded and cancel the stragglers, so a slow/blocked
    source can never make the call hang. Each provider's ``search_timeout`` still
    bounds its own call within the tier."""
    if not providers:
        return []
    tasks = [asyncio.ensure_future(bounded_search(p, name, limit)) for p in providers]
    done, pending = await asyncio.wait(tasks, timeout=deadline)
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)  # let cancellations settle
    results: list[SearchResult] = []
    for t in done:
        try:
            b = t.result()
        except Exception:
            continue
        if isinstance(b, list):
            results.extend(b)
    return results


def split_cost(
    providers: list[SearchProvider],
) -> tuple[list[SearchProvider], list[SearchProvider]]:
    """Partition providers into (free, premium). Premium = the paid/slow Apify
    actors, run only as a cost-gated fallback after the free tier finds nothing."""
    free = [p for p in providers if getattr(p, "cost", None) != "premium"]
    premium = [p for p in providers if getattr(p, "cost", None) == "premium"]
    return free, premium
