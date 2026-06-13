"""Poland — KRS via the Apify actor. Key-gated (APIFY_API_KEY), scoped PL.

Enriches a KRS-number lookup with directors / share capital / PKD / NIP-REGON /
bankruptcy data the official register API does not return. Self-disables when no
Apify token is configured; failures are non-fatal (degrade to []).
"""

from app.config import settings
from app.integrations import apify_krs
from app.search.base import SearchProvider, SearchResult


def _format_capital(cap) -> str | None:
    """Share capital may be a {'amount','currency'} dict or a plain string."""
    if isinstance(cap, dict):
        return " ".join(str(x) for x in (cap.get("amount"), cap.get("currency")) if x) or None
    return str(cap) if cap else None


def _snippet(row: dict) -> str:
    md = row.get("metadata") or {}
    capital = _format_capital(md.get("share_capital"))
    bits = [
        row.get("legal_form"),
        row.get("city"),
        f"NIP {md['nip']}" if md.get("nip") else None,
        f"capital {capital}" if capital else None,
        f"{md['director_count']} directors" if md.get("director_count") else None,
        "in liquidation" if md.get("in_liquidation") else None,
        "bankrupt" if md.get("is_bankrupt") else None,
    ]
    return " · ".join(str(b) for b in bits if b)


class ApifyKrsSearchProvider(SearchProvider):
    name = "apify_krs_pl"
    jurisdictions = {"PL"}
    enabled = True
    cost = "premium"
    lookup = "number"

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        token = settings.apify_api_key
        if not (token and settings.apify_enabled):
            return []  # opt-in: needs the token and the global APIFY_ENABLED flag
        try:
            rows = await apify_krs.search_companies(query, token, limit=limit)
        except Exception:
            return []

        results: list[SearchResult] = []
        for i, r in enumerate(rows[:limit]):
            name = r.get("name")
            if not name:
                continue
            num = r.get("number")
            results.append(
                SearchResult(
                    title=f"{name} ({num})" if num else name,
                    url=r.get("url"),
                    snippet=_snippet(r),
                    score=round(max(0.4, 0.95 - i * 0.05), 4),
                    source=self.name,
                    jurisdiction=r.get("country"),
                    registry_id=str(num) if num else None,
                    register_name=name,
                    organization_type=r.get("legal_form"),
                    status=r.get("status"),
                    incorporation_date=r.get("incorporation_date"),
                    last_update=r.get("last_update"),
                    address=r.get("address"),
                    metadata=r.get("metadata") or {},
                )
            )
        return results
