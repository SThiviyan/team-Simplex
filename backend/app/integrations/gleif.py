"""Client for the GLEIF API (Global Legal Entity Identifier Foundation).

GLEIF publishes the global LEI register: legal entities worldwide, with name,
status, registered address and jurisdiction. The API is free and needs NO
authentication. Docs: https://www.gleif.org/en/lei-data/gleif-api

Shared by both the GLEIF MCP server and the GLEIF search provider so the
request/normalisation logic lives in one place.
"""


from app.integrations.http import rate_limited_get, shared_client

API_BASE = "https://api.gleif.org/api/v1"
# Public, human-readable LEI record page.
RECORD_WEB = "https://search.gleif.org/#/record"

_HEADERS = {"Accept": "application/vnd.api+json"}
# Spacing between GLEIF calls (global per-IP). At ~0.6s (≈100/min) the throttle
# queue for a batch drains roughly twice as fast as the old 1.1s, so rows wait
# less for GLEIF while staying clear of 429s; rate_limited_get still backs off on
# any 429. With the per-provider 60s timeout, queued calls complete rather than
# being cancelled (which is what starved the GLEIF-only jurisdictions).
_MIN_INTERVAL = 0.6


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


def _registered_as(ent: dict) -> str | None:
    """The entity's NATIONAL registry number (e.g. Companies House, KRS, CNPJ).

    GLEIF carries it in entity.registeredAs; data quality varies, so obvious
    placeholder values are dropped rather than surfaced as registry IDs.
    """
    value = (ent.get("registeredAs") or "").strip()
    if not value or value.lower() in {"n/a", "na", "none", "not applicable"}:
        return None
    if set(value) <= {"-", "0"}:  # "---" / "0000" style placeholders
        return None
    return value


def _normalise(rec: dict) -> dict:
    """Flatten one JSON:API lei-record into a compact dict."""
    a = rec.get("attributes", {})
    ent = a.get("entity", {})
    addr = ent.get("legalAddress") or {}
    registration = a.get("registration") or {}
    legal_form = ent.get("legalForm") or {}
    # Only the free-text legal form is human-readable; the bare 4-char ELF code
    # ("ZRPO") is meaningless in the output, so it is not surfaced as a value.
    organization_type = legal_form.get("other") or None
    return {
        "lei": a.get("lei"),
        # National registry number + the registry it was issued by (RA code).
        "registered_as": _registered_as(ent),
        "registration_authority": (ent.get("registeredAt") or {}).get("id"),
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
    """Search the LEI register by name. Returns normalised entity dicts.

    legalName first (best precision); fall back to fulltext only if it found
    nothing. fulltext also matches trading/other names — that's what finds the
    flagship behind an acronym like "PwC" — but it doubles the rate budget, so
    it runs only when needed.
    """
    size = max(1, min(limit, 100))
    page = {"page[size]": size, "page[number]": 1}
    client = shared_client("gleif", base_url=API_BASE, headers=_HEADERS, timeout=20.0)

    async def query(filter_key: str):
        return await rate_limited_get(
            "gleif", client, "/lei-records",
            min_interval=_MIN_INTERVAL, params={filter_key: name, **page},
        )

    def collect(resp, into: list, seen: set) -> None:
        if isinstance(resp, BaseException):
            return  # one search path failing must not kill the other
        resp.raise_for_status()
        for rec in resp.json().get("data", []):
            e = _normalise(rec)
            key = e.get("lei") or e.get("name") or ""
            if key and key not in seen:
                seen.add(key)
                into.append(e)

    # The precise legalName query first. Only fall back to the broader (and
    # rate-budget-doubling) fulltext query when it found nothing — this halves
    # GLEIF's request volume on a batch, so the per-IP throttle queue drains
    # ~2x faster and far fewer calls are cancelled by the gather timeout.
    entities: list[dict] = []
    seen: set[str] = set()
    collect(await query("filter[entity.legalName]"), entities, seen)
    if not entities:
        collect(await query("filter[fulltext]"), entities, seen)
    return entities[:size]


async def get_entity(lei: str) -> dict:
    """Fetch a single LEI record by its 20-character LEI code."""
    client = shared_client("gleif", base_url=API_BASE, headers=_HEADERS, timeout=20.0)
    resp = await rate_limited_get(
        "gleif", client, f"/lei-records/{lei.strip().upper()}", min_interval=_MIN_INTERVAL
    )
    resp.raise_for_status()
    return _normalise(resp.json().get("data", {}))
