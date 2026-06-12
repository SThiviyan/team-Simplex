"""Client for the GLEIF API (Global Legal Entity Identifier Foundation).

GLEIF publishes the global LEI register: legal entities worldwide, with name,
status, registered address and jurisdiction. The API is free and needs NO
authentication. Docs: https://www.gleif.org/en/lei-data/gleif-api

Shared by both the GLEIF MCP server and the GLEIF search provider so the
request/normalisation logic lives in one place.
"""

import httpx

API_BASE = "https://api.gleif.org/api/v1"
# Public, human-readable LEI record page.
RECORD_WEB = "https://search.gleif.org/#/record"

_HEADERS = {"Accept": "application/vnd.api+json"}


def _format_address(addr: dict) -> str | None:
    """Render a GLEIF legalAddress object into a single human-readable line."""
    lines = addr.get("addressLines") or []
    parts = [
        ", ".join(line for line in lines if line),
        addr.get("postalCode"),
        addr.get("city"),
        addr.get("region"),
        addr.get("country"),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _normalise(rec: dict) -> dict:
    """Flatten one JSON:API lei-record into a compact dict."""
    a = rec.get("attributes", {})
    ent = a.get("entity", {})
    addr = ent.get("legalAddress") or {}
    registration = a.get("registration") or {}
    legal_form = ent.get("legalForm") or {}
    # Prefer the free-text legal form ("other"); fall back to the ELF code id.
    organization_type = legal_form.get("other") or legal_form.get("id")
    return {
        "lei": a.get("lei"),
        "name": (ent.get("legalName") or {}).get("name"),
        "entity_status": ent.get("status"),  # ACTIVE / INACTIVE
        "registration_status": registration.get("status"),  # ISSUED / LAPSED / ...
        "city": addr.get("city"),
        "country": addr.get("country"),
        "jurisdiction": ent.get("jurisdiction"),
        "record_url": f"{RECORD_WEB}/{a.get('lei')}",
        # Extra entity context for the final output / frontend.
        "address": _format_address(addr),
        "last_update": registration.get("lastUpdateDate"),
        "organization_type": organization_type,
    }


async def search_entities(name: str, limit: int = 10) -> list[dict]:
    """Search the LEI register by legal name. Returns normalised entity dicts."""
    params = {
        "filter[entity.legalName]": name,
        "page[size]": max(1, min(limit, 100)),
        "page[number]": 1,
    }
    async with httpx.AsyncClient(base_url=API_BASE, headers=_HEADERS, timeout=20.0) as client:
        resp = await client.get("/lei-records", params=params)
        resp.raise_for_status()
        return [_normalise(r) for r in resp.json().get("data", [])]


async def get_entity(lei: str) -> dict:
    """Fetch a single LEI record by its 20-character LEI code."""
    async with httpx.AsyncClient(base_url=API_BASE, headers=_HEADERS, timeout=20.0) as client:
        resp = await client.get(f"/lei-records/{lei.strip().upper()}")
        resp.raise_for_status()
        return _normalise(resp.json().get("data", {}))
