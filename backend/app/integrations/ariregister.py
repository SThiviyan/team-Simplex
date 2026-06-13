"""Client for Estonia's e-Business Register (ariregister.rik.ee).

Free, keyless JSON search run by Estonia's Centre of Registers and Information
Systems (RIK). Searches the Estonian business register by company name.

Shared by the company-registry MCP server (via the provider) and the
AriregisterSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

API = "https://ariregister.rik.ee/est/api/autocomplete"
_UA = "team-simplex-hackathon/1.0 (company search demo)"

# Estonian register status codes -> common vocabulary (others pass through as None).
_STATUS = {"R": "active", "L": "in_liquidation", "K": "dissolved"}


def _normalise(c: dict) -> dict:
    reg = c.get("reg_code")
    return {
        "number": str(reg) if reg else None,  # registrikood (Estonian registry code)
        "name": c.get("name"),
        "legal_form": None,  # only a numeric code; the registered name carries the form (OÜ/AS)
        "city": None,
        "address": c.get("legal_address"),
        "status": _STATUS.get(c.get("status")),
        "country": "EE",
        "court": None,
        "url": c.get("url"),
    }


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Estonian business register by company name (param is `q`)."""
    params = {"q": name, "results": max(1, min(limit, 50))}
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.get(API, params=params)
        resp.raise_for_status()
        return [_normalise(c) for c in resp.json().get("data", [])]
