"""Netherlands KvK (Handelsregister) NAME search via the Apify
'nocodeventure/kvk-handelsregister-scraper' actor.

Real Dutch register name search — the built-in ``kvk`` provider uses KvK's public
TEST key (synthetic data); this returns live data. Key-gated and opt-in via the
global APIFY_ENABLED flag.
"""

from app.integrations.apify import run_actor_get_items

ACTOR = "nocodeventure~kvk-handelsregister-scraper"


def _address(a: dict | None) -> str | None:
    if not a:
        return None
    street = " ".join(
        str(x) for x in (a.get("street"), a.get("houseNumber"), a.get("houseNumberAddition")) if x
    )
    return ", ".join(str(x) for x in (street, a.get("postalCode"), a.get("city")) if x) or None


def to_row(item: dict) -> dict:
    """Map one KvK actor dataset item to the shared register-row shape (+ metadata)."""
    addr = item.get("visitingAddress") or {}
    return {
        "number": item.get("kvkNumber"),
        "name": item.get("name") or item.get("statutoryName"),
        "legal_form": item.get("legalForm"),
        "city": addr.get("city"),
        "status": None if item.get("isActive") else "dissolved",
        "incorporation_date": None,
        "country": "NL",
        "address": _address(addr),
        "url": (
            f"https://www.kvk.nl/zoeken/?source=all&q={item['kvkNumber']}"
            if item.get("kvkNumber")
            else None
        ),
        "metadata": {
            "establishment_number": item.get("establishmentNumber"),
            "is_active": bool(item.get("isActive")),
            "registration_type": item.get("registrationType"),
            "trade_names": (item.get("currentTradeNames") or [])[:5],
            "activity": (item.get("activityDescription") or "").strip()[:160] or None,
        },
    }


async def search_companies(
    query: str, token: str, *, limit: int = 10, timeout: float = 90.0
) -> list[dict]:
    """Name-search the Dutch KvK register via the Apify actor."""
    if not query:
        return []
    items = await run_actor_get_items(
        ACTOR,
        {"searchQuery": query, "maxPages": 1, "pageSize": max(1, min(limit, 10))},
        token,
        timeout=timeout,
    )
    return [to_row(it) for it in items if it.get("name") or it.get("statutoryName")]
