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
    return {
        "lei": a.get("lei"),
        "name": (ent.get("legalName") or {}).get("name"),
        "entity_status": ent.get("status"),  # ACTIVE / INACTIVE
        "registration_status": registration.get("status"),  # ISSUED / LAPSED / ...
        "city": addr.get("city"),
        "country": addr.get("country"),
        "jurisdiction": ent.get("jurisdiction"),
        "record_url": f"{RECORD_WEB}/{a.get('lei')}",
        # The official registration number in the home registry (e.g. "HRB 719915")
        # and the registration-authority code that issued it (e.g. "RA000296").
        # registeredAs is the registry's own ID for the entity — NOT the LEI.
        "registered_as": ent.get("registeredAs"),
        "ra_code": (ent.get("registeredAt") or {}).get("id"),
        # Resolved from ra_code below (cached): the specific court / registry
        # office, e.g. "Amtsgericht Mannheim".
        "registry_court": None,
        # Extra entity context for the final output / frontend.
        "address": _format_address(addr),
        "last_update": registration.get("lastUpdateDate"),
        # Free-text legal form when GLEIF has one; else the ELF code (resolved to
        # a readable name below, e.g. "6QQB" -> "Aktiengesellschaft").
        "organization_type": legal_form.get("other"),
        "elf_code": legal_form.get("id"),
        # The entity's incorporation/creation date and its operating status.
        "incorporation_date": ent.get("creationDate"),
        "status": ent.get("status"),  # ACTIVE / INACTIVE / NULL
    }


# RA code -> court/office name, cached per process (the set of registration
# authorities is small and stable, and results reuse the same codes heavily).
_RA_OFFICE_CACHE: dict[str, str | None] = {}


async def _registration_office(client: httpx.AsyncClient, code: str | None) -> str | None:
    """Resolve a GLEIF registration-authority code (e.g. "RA000296") to the
    specific court or registry office (e.g. "Amtsgericht Mannheim").

    Returns None on any failure — never fatal to the search.
    """
    if not code:
        return None
    if code in _RA_OFFICE_CACHE:
        return _RA_OFFICE_CACHE[code]
    office: str | None = None
    try:
        resp = await client.get(f"/registration-authorities/{code}")
        if resp.status_code == 200:
            attr = resp.json().get("data", {}).get("attributes", {})
            # Prefer the specific local office name (the court); fall back to the
            # international name, then the registry name.
            office = (
                attr.get("localOrganizationName")
                or attr.get("internationalOrganizationName")
                or attr.get("localName")
                or attr.get("internationalName")
            )
    except Exception:
        office = None
    _RA_OFFICE_CACHE[code] = office
    return office


# ELF code -> legal-form name, cached per process (ISO 20275 codes are small and
# stable, e.g. "6QQB" -> "Aktiengesellschaft").
_ELF_NAME_CACHE: dict[str, str | None] = {}


async def _legal_form_name(client: httpx.AsyncClient, code: str | None) -> str | None:
    """Resolve a GLEIF/ISO-20275 entity-legal-form code to its readable name.

    Returns None on any failure — never fatal to the search.
    """
    if not code:
        return None
    if code in _ELF_NAME_CACHE:
        return _ELF_NAME_CACHE[code]
    name: str | None = None
    try:
        resp = await client.get(f"/entity-legal-forms/{code}")
        if resp.status_code == 200:
            names = resp.json().get("data", {}).get("attributes", {}).get("names", [])
            if names:
                name = names[0].get("localName") or names[0].get("transliteratedName")
    except Exception:
        name = None
    _ELF_NAME_CACHE[code] = name
    return name


async def _enrich(client: httpx.AsyncClient, entities: list[dict]) -> list[dict]:
    """Resolve each entity's authority code to a court name and its ELF code to a
    readable legal-form name (when GLEIF didn't provide a free-text one)."""
    for e in entities:
        e["registry_court"] = await _registration_office(client, e.pop("ra_code", None))
        elf = e.pop("elf_code", None)
        if not e.get("organization_type"):
            e["organization_type"] = await _legal_form_name(client, elf)
    return entities


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
        entities = [_normalise(r) for r in resp.json().get("data", [])]
        return await _enrich(client, entities)


async def get_entity(lei: str) -> dict:
    """Fetch a single LEI record by its 20-character LEI code."""
    async with httpx.AsyncClient(base_url=API_BASE, headers=_HEADERS, timeout=20.0) as client:
        resp = await client.get(f"/lei-records/{lei.strip().upper()}")
        resp.raise_for_status()
        entity = _normalise(resp.json().get("data", {}))
        (entity,) = await _enrich(client, [entity])
        return entity
