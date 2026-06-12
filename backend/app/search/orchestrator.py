import asyncio

from app.search.base import SearchProvider, SearchResult


class FederatedSearch:
    """Fans out across providers in parallel, merges by score.

    Teams: this is a starting point, not a contract. Replace it, extend it,
    or remove it entirely. Suggested directions: caching, deduplication,
    query rewriting, semantic reranking, hybrid retrieval, etc.
    """

    def __init__(self, providers: list[SearchProvider]):
        self.providers = providers

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        per_provider = await asyncio.gather(
            *(p.search(query, limit=limit) for p in self.providers),
            return_exceptions=True,
        )

        merged: list[SearchResult] = []
        for batch in per_provider:
            if isinstance(batch, Exception):
                # Provider failures are non-fatal in federated search.
                continue
            merged.extend(batch)

        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:limit]
