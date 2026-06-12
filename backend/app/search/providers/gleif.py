"""GLEIF (global LEI register) search provider.

GLEIF needs NO credentials and NO LLM — it calls the free public API directly,
so it always returns real results. It is the keyless default search provider.
"""

from app.integrations import gleif
from app.search.base import SearchProvider, SearchResult


def _score(query: str, name: str | None, rank: int) -> float:
    """Heuristic match strength so the orchestrator ranks exact names highest."""
    q = query.strip().lower()
    n = (name or "").strip().lower()
    if n == q:
        return 1.0
    if n.startswith(q):
        return 0.9
    base = 0.8 if q in n else 0.7
    return round(max(0.3, base - rank * 0.02), 4)


def _snippet(e: dict) -> str:
    place = ", ".join(p for p in (e.get("city"), e.get("country")) if p)
    bits = [
        f"LEI {e['lei']}" if e.get("lei") else None,
        e.get("entity_status"),
        place or None,
        f"jurisdiction {e['jurisdiction']}" if e.get("jurisdiction") else None,
    ]
    return " · ".join(b for b in bits if b)


class GleifSearchProvider(SearchProvider):
    name = "gleif"
    # Keyless and LLM-free, so it is always available.
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            entities = await gleif.search_entities(query, limit=limit)
        except Exception:
            # Federated search treats provider failures as non-fatal.
            return []

        results: list[SearchResult] = []
        for i, e in enumerate(entities[:limit]):
            if not e.get("name"):
                continue
            results.append(
                SearchResult(
                    title=f"{e['name']} ({e['lei']})" if e.get("lei") else e["name"],
                    url=e.get("record_url"),
                    snippet=_snippet(e),
                    score=_score(query, e.get("name"), i),
                    source=self.name,
                    jurisdiction=e.get("country"),
                    registry_id=e.get("lei"),
                    register_name=e.get("name"),
                    last_update=e.get("last_update"),
                    address=e.get("address"),
                    organization_type=e.get("organization_type"),
                )
            )
        return results
