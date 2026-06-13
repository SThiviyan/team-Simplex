"""Short-TTL memo for provider searches.

Within one pipeline run, the same (provider, query) is often searched more than
once: the agent retries a name across tool rounds and MCP entries, the
recursion round repeats names already tried, and batches contain duplicate
companies. Registry content does not change within minutes, so an identical
query gets the memoized response — byte-identical to a live call, minus the
network round-trip.

TTL is deliberately short (5 minutes ≈ one batch run); failures are never
cached, and an empty result IS cached (a registry that just returned nothing
for this exact query will return nothing again 30 seconds later).
"""

import time

from app.search.base import SearchProvider, SearchResult

TTL_SECONDS = 300.0
_MAX_ENTRIES = 2048

_cache: dict[tuple[str, str, int], tuple[float, list[SearchResult]]] = {}


def clear() -> None:
    _cache.clear()


def _prune() -> None:
    if len(_cache) <= _MAX_ENTRIES:
        return
    now = time.monotonic()
    for key in [k for k, (ts, _) in _cache.items() if now - ts >= TTL_SECONDS]:
        _cache.pop(key, None)
    while len(_cache) > _MAX_ENTRIES:  # still full of fresh entries — drop oldest
        _cache.pop(next(iter(_cache)))


async def cached_search(provider: SearchProvider, name: str, limit: int) -> list[SearchResult]:
    key = (provider.name, name.strip().lower(), limit)
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1]

    # On a cold process (in-memory miss), try the on-disk cache: re-running the
    # same query then skips the slow source (scrape, rate-limited GLEIF) entirely.
    from app.config import settings
    from app.search import persistent_cache

    ns = f"provider:{provider.name}:{limit}"
    disk = persistent_cache.get(ns, key[1], max_age=settings.search_cache_ttl_seconds)
    if disk is not None:
        results = [SearchResult(**r) for r in disk]
        _cache[key] = (time.monotonic(), results)
        return results

    results = await provider.search(name, limit=limit)  # exceptions propagate, uncached
    _cache[key] = (time.monotonic(), results)
    _prune()
    persistent_cache.set(ns, key[1], [r.model_dump() for r in results])
    return results
