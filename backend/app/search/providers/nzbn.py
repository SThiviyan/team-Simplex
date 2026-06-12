"""NZBN (New Zealand Companies Office / business register) search provider.

Wraps the NZBN API client. Jurisdiction-scoped to NZ, so the resolver only calls
it when the requested jurisdiction is New Zealand. Needs a free subscription key
(settings.nzbn_api_key); when unset the provider disables itself and the keyless
sources still work. Failures are non-fatal (degrade to []).
"""

from app.config import settings
from app.integrations import nzbn
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
    bits = [
        f"NZBN {e['nzbn']}" if e.get("nzbn") else None,
        e.get("status"),
        e.get("organization_type"),
        e.get("address"),
    ]
    return " · ".join(b for b in bits if b)


class NzbnSearchProvider(SearchProvider):
    name = "nzbn"
    jurisdictions = {"NZ"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        key = settings.nzbn_api_key
        if not key:
            # No key configured — the provider disables itself cleanly.
            return []
        try:
            rows = await nzbn.search_entities(query, key, limit=limit)
        except Exception:
            # Federated search treats provider failures as non-fatal.
            return []

        results: list[SearchResult] = []
        for i, e in enumerate(rows[:limit]):
            if not e.get("name"):
                continue
            num = e.get("registration_number")
            results.append(
                SearchResult(
                    title=f"{e['name']} ({num})" if num else e["name"],
                    url=e.get("record_url"),
                    snippet=_snippet(e),
                    score=_score(query, e.get("name"), i),
                    source=self.name,
                    jurisdiction="NZ",  # NZ register by definition
                    registry_id=num,
                    register_name=e.get("name"),
                    address=e.get("address"),
                    organization_type=e.get("organization_type"),
                    last_update=e.get("last_update"),
                )
            )
        return results
