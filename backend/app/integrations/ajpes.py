"""Client for the AJPES restPrsInfo API (Slovenian Business Register, PRS).

AJPES is the Slovenian agency that runs the Primary Business Register (Poslovni
register Slovenije). Its restPrsInfo service (REST, replaced the old SOAP one in
2026) searches business entities. Access needs AJPES-issued credentials
(username/password) and an authorised data set code ("shema").

API: https://www.ajpes.si/restPrsInfo (swagger at /restPrsInfo/swagger)

Shared by the company-registry MCP server (via the provider) and the
AjpesSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

API_BASE = "https://www.ajpes.si/restPrsInfo"
# Public ePRS lookup page (by registration number / maticna).
RECORD_WEB = "https://www.ajpes.si/prs/podjetjeSP.asp?m"


def _normalise(row: dict) -> dict:
    """Flatten one PRS find-result row (PrsDataType) into a compact dict."""
    maticna = row.get("maticna")
    # popolno_ime = full registered name; kratko_ime = short name.
    name = row.get("popolno_ime") or row.get("kratko_ime")
    address = ", ".join(p for p in (row.get("ulica"), row.get("posta")) if p) or None
    return {
        "name": name,
        # The Slovenian registration number (matična številka) — the official
        # PRS registry identifier.
        "registration_number": maticna,
        "address": address,
        "branch": row.get("podenota"),
        # SI register; ISO 3166-1 alpha-2 so the jurisdiction filter matches "SI".
        "jurisdiction": "SI",
        "record_url": f"{RECORD_WEB}={maticna}" if maticna else None,
    }


def _rows(payload: dict) -> list[dict]:
    """Pull the prsData rows out of the (raw | formatted | flat) payload shapes."""
    if not isinstance(payload, dict):
        return []
    for container in (payload.get("raw"), payload.get("formatted"), payload):
        if isinstance(container, dict) and isinstance(container.get("prsData"), list):
            return container["prsData"]
    return []


async def find_entities(
    name: str,
    user: str,
    password: str,
    schema: str | None,
    limit: int = 10,
) -> list[dict]:
    """Search the PRS by name (naziv). Returns normalised entity dicts."""
    body = {
        "ident": {"uporabnik": user, "geslo": password, "shema": schema or ""},
        "naziv": name,
        "maxRecords": max(1, min(limit, 100)),
    }
    async with httpx.AsyncClient(base_url=API_BASE, timeout=20.0) as client:
        resp = await client.post("/find", json=body)
        resp.raise_for_status()
        rows = _rows(resp.json().get("payload", {}))
        return [_normalise(r) for r in rows]
