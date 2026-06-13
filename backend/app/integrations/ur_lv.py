"""Client for Latvia's Register of Enterprises (Uzņēmumu reģistrs).

Free, keyless CKAN open data published on data.gov.lv by the Latvian Register of
Enterprises. Full-text name search over the register of legal entities.

Shared by the company-registry MCP server (via the provider) and the
UrLvSearchProvider so request/normalisation logic lives in one place.
"""

from app.integrations.http import shared_client

API = "https://data.gov.lv/dati/lv/api/3/action/datastore_search"
# "Uzņēmumu reģistra atvērtie dati" — the register-of-enterprises datastore.
RESOURCE_ID = "25e80bf3-f107-4ab4-89ef-251b5b9374e9"
WEB = "https://www.ur.gov.lv"  # portal (no stable per-entity permalink)
_UA = "team-simplex-hackathon/1.0 (company search demo)"


def _status(r: dict) -> str | None:
    if (r.get("terminated") or "").strip() or (r.get("closed") or "").strip():
        return "dissolved"
    if (r.get("registered") or "").strip():
        return "active"
    return None


def _normalise(r: dict) -> dict:
    code = r.get("regcode")
    return {
        "number": str(code) if code else None,  # reģistrācijas numurs
        "name": r.get("name"),
        "legal_form": r.get("type_text") or r.get("name_after_quotes") or None,
        "city": r.get("city"),
        "address": r.get("address"),
        "status": _status(r),
        "incorporation_date": (r.get("registered") or "").strip() or None,
        "country": "LV",
        "court": None,
        "url": None,
    }


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Latvian register of enterprises by name (keyless CKAN)."""
    params = {"resource_id": RESOURCE_ID, "q": name, "limit": max(1, min(limit, 50))}
    client = shared_client(
        "ur_lv", timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    )
    resp = await client.get(API, params=params)
    if resp.status_code != 200:
        return []
    data = resp.json()
    if not data.get("success"):
        return []
    return [_normalise(r) for r in data.get("result", {}).get("records", [])[:limit]]
