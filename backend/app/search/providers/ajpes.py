"""AJPES (Slovenian Business Register, PRS) search provider.

Wraps the AJPES restPrsInfo client. Jurisdiction-scoped to SI, so the resolver
only calls it when the requested jurisdiction is Slovenia. Needs AJPES-issued
credentials (settings.ajpes_user + settings.ajpes_password); when either is unset
the provider disables itself and the keyless sources still work. Failures are
non-fatal (degrade to []).
"""

from app.config import settings
from app.integrations import ajpes
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
        f"Matična {e['registration_number']}" if e.get("registration_number") else None,
        e.get("branch"),
        e.get("address"),
    ]
    return " · ".join(b for b in bits if b)


class AjpesSearchProvider(SearchProvider):
    name = "ajpes"
    jurisdictions = {"SI"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        user, password = settings.ajpes_user, settings.ajpes_password
        if not user or not password:
            # No credentials configured — the provider disables itself cleanly.
            return []
        try:
            rows = await ajpes.find_entities(
                query, user, password, settings.ajpes_schema, limit=limit
            )
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
                    jurisdiction="SI",  # Slovenian register by definition
                    registry_id=num,
                    register_name=e.get("name"),
                    address=e.get("address"),
                )
            )
        return results
