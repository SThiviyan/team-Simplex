"""NorthData company search via the Apify 'powerai/northdata-search-scraper' actor.

NorthData (northdata.com) does NAME search across DACH + broader Europe, returning
the registering authority and registration number in a `summary` string. The actor
takes a NorthData *search URL*, which we build from the company name. Key-gated
(Apify token) and slow (~20s cold), so the provider is opt-in.
"""

import re
from urllib.parse import quote

from app.integrations.apify import run_actor_get_items

ACTOR = "powerai~northdata-search-scraper"
_SEARCH = "https://www.northdata.com/_search?query="

# English country name (NorthData's name suffix) -> ISO 3166-1 alpha-2.
_COUNTRY = {
    "germany": "DE", "austria": "AT", "switzerland": "CH", "belgium": "BE",
    "netherlands": "NL", "france": "FR", "luxembourg": "LU", "united kingdom": "GB",
    "ireland": "IE", "italy": "IT", "spain": "ES", "portugal": "PT", "poland": "PL",
    "czechia": "CZ", "czech republic": "CZ", "denmark": "DK", "sweden": "SE",
    "norway": "NO", "finland": "FI", "liechtenstein": "LI", "united states": "US",
    "hungary": "HU", "slovakia": "SK", "slovenia": "SI", "romania": "RO",
}


def search_url(name: str) -> str:
    return _SEARCH + quote(name)


def _split_name(full: str) -> tuple[str, str | None, str | None]:
    """'Siemens N.V., Beersel, Belgium' -> ('Siemens N.V.', 'Beersel', 'BE').

    NorthData ends the name with either a full country name ('Belgium') or an
    ISO code ('DE', 'ZA') — handle both.
    """
    parts = [p.strip() for p in (full or "").split(",") if p.strip()]
    if not parts:
        return full, None, None
    company = parts[0]
    country = None
    if len(parts) >= 2:
        last = parts[-1]
        if len(last) == 2 and last.isalpha():
            country = last.upper()
        else:
            country = _COUNTRY.get(last.lower())
    city = parts[1] if len(parts) >= 3 else None
    return company, city, country


def _parse_summary(summary: str | None) -> tuple[str | None, str | None]:
    """'Crossroads Bank for Enterprises (KBO) 0404.284.716' -> (id, court)."""
    if not summary:
        return None, None
    m = re.search(r"([A-Z]{0,4}[\s\-]?\d[\d.\-/ ]*\d)\s*$", summary)
    if not m:
        return None, summary or None
    return m.group(1).strip(), (summary[: m.start()].strip() or None)


def to_row(item: dict) -> dict:
    company, city, country = _split_name(item.get("name") or "")
    registry_id, court = _parse_summary(item.get("summary"))
    details = item.get("dataDetails") or {}
    suffix = (item.get("name") or "").split(",")[1:]
    return {
        "number": registry_id,
        "name": company,
        "court": court,
        "city": city,
        "country": country,
        "url": item.get("detailPageUrl"),
        "snippet": item.get("summary"),
        "address": ", ".join(p.strip() for p in suffix if p.strip()) or None,
        "metadata": {
            "northdata_score": details.get("score"),
            "name_score": details.get("nameScore"),
            "lat": item.get("lat"),
            "lng": item.get("lng"),
            "siren": item.get("siren"),
        },
    }


async def search_companies(
    query: str, token: str, *, limit: int = 10, timeout: float = 120.0
) -> list[dict]:
    """Name-search NorthData via the Apify actor and map to register-row dicts."""
    if not query:
        return []
    items = await run_actor_get_items(
        ACTOR,
        {"searchUrl": search_url(query), "maxItems": max(1, min(limit, 10))},
        token,
        timeout=timeout,
    )
    return [to_row(it) for it in items if it.get("name")]
