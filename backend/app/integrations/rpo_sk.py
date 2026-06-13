"""Client for Slovakia's RPO — Register of Legal Entities (api.statistics.sk).

Free, keyless JSON API run by the Statistical Office of the Slovak Republic.
Searches all legal entities by name. Records carry temporal arrays (names,
identifiers, addresses with validFrom/validTo) — we pick the current value.

Shared by the company-registry MCP server (via the provider) and the
RpoSkSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

API = "https://api.statistics.sk/rpo/v1/search"
_UA = "team-simplex-hackathon/1.0 (company search demo)"


def _current(items: list[dict]) -> dict | None:
    """Pick the currently-valid entry from a temporal array (no validTo), else
    the latest by validFrom."""
    if not items:
        return None
    valid = [i for i in items if not i.get("validTo")]
    pool = valid or items
    return max(pool, key=lambda i: i.get("validFrom") or "")


def _address(addr: dict | None) -> str | None:
    if not addr:
        return None
    line = " ".join(p for p in (addr.get("street"), addr.get("buildingNumber")) if p)
    postal = (addr.get("postalCodes") or [None])[0]
    municipality = (addr.get("municipality") or {}).get("value")
    tail = " ".join(p for p in (postal, municipality) if p)
    return ", ".join(p for p in (line or None, tail or None) if p) or None


def _normalise(r: dict) -> dict:
    ident = _current(r.get("identifiers") or [])
    name = _current(r.get("fullNames") or [])
    addr = _current(r.get("addresses") or [])
    return {
        "number": ident.get("value") if ident else None,  # IČO
        "name": name.get("value") if name else None,
        "legal_form": None,  # the registered name carries the form (s.r.o. / a.s.)
        "city": (addr.get("municipality") or {}).get("value") if addr else None,
        "address": _address(addr),
        "status": None,
        "incorporation_date": r.get("establishment"),
        "country": "SK",
        "court": None,
        "url": None,
    }


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Slovak register of legal entities by name (fullName)."""
    params = {"fullName": name}
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.get(API, params=params)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [_normalise(r) for r in results[:limit]]
