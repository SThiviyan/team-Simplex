"""Poland KRS via the Apify 'parseforge/krs-poland-scraper' actor (key-gated).

Returns RICHER data than the official KRS API client (``krs_pl``): directors,
share capital, PKD industry codes, NIP/REGON, and bankruptcy / liquidation flags.
Like the official client it resolves by KRS number (KYC inputs usually carry it);
a name-only query yields nothing. Requires an Apify token — the provider
self-disables without one.
"""

import re

from app.integrations.apify import run_actor_get_items
from app.integrations.krs_pl import extract_krs

ACTOR = "parseforge~krs-poland-scraper"


def _iso_date(pl_date: str | None) -> str | None:
    """Polish ``DD.MM.YYYY`` -> ISO ``YYYY-MM-DD`` (pass-through otherwise)."""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", pl_date or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else (pl_date or None)


def _status(item: dict) -> str | None:
    if item.get("inLiquidation"):
        return "in_liquidation"
    if item.get("isBankrupt"):
        return "dissolved"  # normalised vocab; flagged as bankrupt in metadata too
    return None


def _address(item: dict) -> str | None:
    street = " ".join(str(p) for p in (item.get("street"), item.get("houseNumber")) if p)
    return ", ".join(str(x) for x in (street, item.get("postalCode"), item.get("city")) if x) or None


def to_row(item: dict) -> dict:
    """Map one actor dataset item to the shared register-row shape (+ metadata)."""
    krs = item.get("krsNumber")
    pkd = item.get("pkdCodes") or []
    directors = item.get("directors") or []
    return {
        "number": krs,
        "name": item.get("name"),
        "legal_form": item.get("legalForm"),
        "city": item.get("city"),
        "status": _status(item),
        "incorporation_date": _iso_date(item.get("registrationDate")),
        "last_update": _iso_date(item.get("lastUpdateDate")),
        "country": "PL",
        "address": _address(item),
        "url": f"https://wyszukiwarka-krs.ms.gov.pl/podmiot/{krs}" if krs else None,
        # Rich extras the official API does not return -> SearchResult.metadata.
        "metadata": {
            "nip": item.get("nip"),
            "regon": item.get("regon"),
            "share_capital": item.get("shareCapital"),
            "pkd_codes": pkd[:5] if isinstance(pkd, list) else pkd,
            "director_count": len(directors) if isinstance(directors, list) else None,
            "in_liquidation": bool(item.get("inLiquidation")),
            "is_bankrupt": bool(item.get("isBankrupt")),
        },
    }


async def search_companies(
    query: str, token: str, *, limit: int = 10, timeout: float = 90.0
) -> list[dict]:
    """Resolve a Polish company by the KRS number in the query via the Apify actor."""
    krs = extract_krs(query)
    if not krs:
        return []  # no public name search — by KRS number only
    items = await run_actor_get_items(
        ACTOR,
        {"krsNumbers": [krs], "registry": "P", "maxItems": max(1, min(limit, 10))},
        token,
        timeout=timeout,
    )
    return [to_row(it) for it in items if it.get("name")]
