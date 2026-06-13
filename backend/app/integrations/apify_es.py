"""Spain — company directory (Registro Mercantil) NAME search via the Apify
'regdata/spain-company-directory-scraper' actor. Slow (captcha-bound). Opt-in.
"""

from app.integrations.apify import run_actor_get_items

ACTOR = "regdata~spain-company-directory-scraper"


def _status(s: str | None) -> str | None:
    if not s:
        return None
    return None if s.lower().startswith("vigente") else "dissolved"


def to_row(it: dict) -> dict:
    return {
        "number": it.get("nif"),
        "name": it.get("companyName"),
        "legal_form": it.get("legalForm"),
        "city": it.get("province"),
        "status": _status(it.get("status")),
        "incorporation_date": None,
        "country": "ES",
        "court": it.get("registroMercantil"),
        "address": it.get("address"),
        "url": it.get("sourceUrl"),
        "snippet": " · ".join(
            str(x)
            for x in (it.get("legalForm"), it.get("province"), it.get("cnaeDescription"), it.get("status"))
            if x
        ),
        "metadata": {"euid": it.get("euid"), "cnae": it.get("cnaeDescription"), "irus": it.get("irus")},
    }


async def search_companies(query: str, token: str, *, limit: int = 10, timeout: float = 180.0) -> list[dict]:
    if not query:
        return []
    items = await run_actor_get_items(
        ACTOR, {"searchQuery": query, "maxResults": max(1, min(limit, 10))}, token, timeout=timeout
    )
    return [to_row(it) for it in items if it.get("companyName")][:limit]
