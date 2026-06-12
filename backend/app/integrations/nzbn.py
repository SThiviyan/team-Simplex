"""Client for the New Zealand Business Number (NZBN) API.

The NZBN register, run by MBIE, covers every entity on the NZ Companies Office
registers plus other registered businesses. The v5 API needs a free subscription
key (portal.api.business.govt.nz) sent in the Ocp-Apim-Subscription-Key header.

Docs: https://portal.api.business.govt.nz/api/nzbn

Shared by the company-registry MCP server (via the provider) and the
NzbnSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

API_BASE = "https://api.business.govt.nz/gateway/nzbn/v5"
# Public, human-readable NZBN detail page.
RECORD_WEB = "https://www.nzbn.govt.nz/mynzbn/nzbndetails"


def _format_address(entity: dict) -> str | None:
    """Pick the registered/physical address line from the entity's addresses."""
    addresses = (entity.get("addresses") or {}).get("addressList") or []
    # Prefer the registered address, else the first one available.
    chosen = next(
        (a for a in addresses if (a.get("purpose") or "").upper() == "REGISTERED"),
        addresses[0] if addresses else None,
    )
    return (chosen or {}).get("fullAddress") or None


def _normalise(entity: dict) -> dict:
    """Flatten one NZBN entity into a compact dict."""
    nzbn = entity.get("nzbn")
    # The official registration number in the *source* register (the Companies
    # Office company number for companies); fall back to the NZBN itself.
    registration_number = entity.get("sourceRegisterUniqueIdentifier") or nzbn
    return {
        "nzbn": nzbn,
        "name": entity.get("entityName"),
        "registration_number": registration_number,
        "status": entity.get("entityStatusDescription") or entity.get("entityStatusCode"),
        "organization_type": entity.get("entityTypeDescription") or entity.get("entityTypeCode"),
        "address": _format_address(entity),
        "incorporation_date": entity.get("registrationDate"),
        "last_update": entity.get("lastUpdatedDate") or entity.get("registrationDate"),
        # NZ register; ISO 3166-1 alpha-2 so the jurisdiction filter matches "NZ".
        "jurisdiction": "NZ",
        "record_url": f"{RECORD_WEB}/{nzbn}/" if nzbn else None,
    }


async def search_entities(name: str, api_key: str, limit: int = 10) -> list[dict]:
    """Search the NZBN register by entity name. Returns normalised entity dicts."""
    headers = {"Ocp-Apim-Subscription-Key": api_key, "Accept": "application/json"}
    params = {"search-term": name, "page-size": max(1, min(limit, 100)), "page": 1}
    async with httpx.AsyncClient(base_url=API_BASE, headers=headers, timeout=20.0) as client:
        resp = await client.get("/entities", params=params)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return [_normalise(e) for e in items]


async def get_entity(nzbn: str, api_key: str) -> dict:
    """Fetch a single entity by its 13-digit NZBN."""
    headers = {"Ocp-Apim-Subscription-Key": api_key, "Accept": "application/json"}
    async with httpx.AsyncClient(base_url=API_BASE, headers=headers, timeout=20.0) as client:
        resp = await client.get(f"/entities/{nzbn.strip()}")
        resp.raise_for_status()
        return _normalise(resp.json())
