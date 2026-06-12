"""Companies House (UK commercial register) search provider.

Wraps the Companies House Public Data API client. Jurisdiction-scoped to GB, so
the resolver only calls it when the requested jurisdiction is the UK. Needs a
free REST key (settings.uk_company_house_key); when the key is unset the
provider disables itself and the keyless sources still work. Failures are
non-fatal (degrade to []), same as the other providers.
"""

from app.config import settings
from app.integrations import companies_house
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


def _snippet(r: dict) -> str:
    bits = [
        f"No. {r['company_number']}" if r.get("company_number") else None,
        r.get("status"),
        r.get("organization_type"),
        r.get("address"),
    ]
    return " · ".join(b for b in bits if b)


class CompaniesHouseSearchProvider(SearchProvider):
    name = "companies_house"
    jurisdictions = {"GB"}
    enabled = True

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        key = settings.uk_company_house_key
        if not key:
            # No key configured — the provider disables itself cleanly.
            return []
        try:
            rows = await companies_house.search_companies(query, key, limit=limit)
        except Exception:
            # Federated search treats provider failures as non-fatal.
            return []

        results: list[SearchResult] = []
        for i, r in enumerate(rows[:limit]):
            if not r.get("name"):
                continue
            num = r.get("company_number")
            results.append(
                SearchResult(
                    title=f"{r['name']} ({num})" if num else r["name"],
                    url=r.get("record_url"),
                    snippet=_snippet(r),
                    score=_score(query, r.get("name"), i),
                    source=self.name,
                    jurisdiction="GB",  # UK register by definition
                    registry_id=num,
                    register_name=r.get("name"),
                    address=r.get("address"),
                    organization_type=r.get("organization_type"),
                )
            )
        return results
