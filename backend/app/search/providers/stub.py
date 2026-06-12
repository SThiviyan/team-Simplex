import hashlib

from app.search.base import SearchProvider, SearchResult


class StubSearchProvider(SearchProvider):
    """Deterministic placeholder. Replace with a real provider during the hackathon.

    Returns synthetic results derived from the query string so smoke tests can
    assert non-empty, stable output.
    """

    def __init__(self, name: str = "stub"):
        self.name = name

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        seed = int(hashlib.md5(f"{self.name}:{query}".encode()).hexdigest()[:8], 16)
        n = min(limit, 5)
        return [
            SearchResult(
                title=f"[{self.name}] Result {i + 1} for '{query}'",
                url=f"https://example.invalid/{self.name}/{seed}-{i}",
                snippet=f"Stub snippet {i + 1}. Replace this provider with a real one.",
                score=max(0.0, 1.0 - i * 0.15),
                source=self.name,
            )
            for i in range(n)
        ]
