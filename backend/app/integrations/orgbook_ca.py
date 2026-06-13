"""Client for OrgBook BC — British Columbia's public organisation registry.

Free, keyless JSON API run by the Government of British Columbia (Canada). Holds
BC-registered companies, societies and extraprovincial registrations. Search by
name returns "topics" (organisations) with credentialed attributes.

API: https://orgbook.gov.bc.ca/api/v4/search/topic

Shared by the company-registry MCP server (via the provider) and the
OrgbookCaSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

API = "https://orgbook.gov.bc.ca/api/v4/search/topic"
WEB = "https://orgbook.gov.bc.ca/entity"
_UA = "team-simplex-hackathon/1.0 (company search demo)"

# BC entity_status codes -> common vocabulary.
_STATUS = {
    "ACT": "active",
    "HIS": "dissolved",
    "HLD": "active",
    "CAN": "dissolved",
    "DIS": "dissolved",
    "LIQ": "in_liquidation",
}


def _attrs(t: dict) -> dict:
    return {a.get("type"): a.get("value") for a in (t.get("attributes") or [])}


def _name(t: dict) -> str | None:
    names = t.get("names") or []
    return names[0].get("text") if names else None


def _address(t: dict) -> str | None:
    addrs = t.get("addresses") or []
    if not addrs:
        return None
    a = addrs[0]
    if a.get("text"):
        return a["text"]
    parts = (a.get("civic_address"), a.get("city"), a.get("province"), a.get("postal_code"))
    return ", ".join(p for p in parts if p) or None


def _normalise(t: dict) -> dict:
    at = _attrs(t)
    sid = t.get("source_id")
    status = at.get("entity_status")
    return {
        "number": sid,  # BC registration number, e.g. "FM0145406"
        "name": _name(t),
        "legal_form": at.get("entity_type"),
        "city": None,
        "address": _address(t),
        "status": _STATUS.get(status, status),
        "incorporation_date": at.get("registration_date"),
        "country": "CA",
        "court": None,
        "url": f"{WEB}/{sid}" if sid else None,
    }


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the BC registry by organisation name."""
    params = {"q": name, "inactive": "false"}
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.get(API, params=params)
        resp.raise_for_status()
        return [_normalise(t) for t in resp.json().get("results", [])[:limit]]
