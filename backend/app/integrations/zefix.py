"""Client for Zefix — the Swiss central business name index (zefix.admin.ch).

Zefix aggregates all cantonal commercial registers and is the authoritative
source for the Swiss UID (CHE-xxx.xxx.xxx). The Public REST API is FREE but now
token-gated: register at https://www.zefix.admin.ch (HTTP Basic credentials).
The provider self-disables when no credentials are configured (same convention
as Companies House / KVK), so the keyless stack keeps working without it.

Endpoint: POST https://www.zefix.admin.ch/ZefixPublicREST/api/v1/firm/search.json
Body:     {"name": "<query>", "languageKey": "en", "maxEntries": <n>}
Auth:     HTTP Basic (registered Zefix account).
"""

import httpx

API = "https://www.zefix.admin.ch/ZefixPublicREST/api/v1/firm/search.json"
# Public, human-readable firm page (UID without dashes/dots in the path).
FIRM_WEB = "https://www.zefix.ch/en/search/entity/list"
_UA = "team-simplex-hackathon/1.0 (company search demo)"

# Zefix status vocabulary -> our canonical status.
_STATUS = {
    "ACTIVE": "active",
    "CANCELLED": "dissolved",
    "DELETED": "dissolved",
    "BEING_CANCELLED": "in_liquidation",
}


def _normalise(firm: dict) -> dict:
    uid = firm.get("uid")  # "CHE-123.456.789"
    legal_form = firm.get("legalForm") or {}
    return {
        "number": uid,
        "name": firm.get("name"),
        "legal_form": legal_form.get("name") if isinstance(legal_form, dict) else None,
        "city": firm.get("legalSeat"),
        "address": firm.get("legalSeat"),
        "status": _STATUS.get((firm.get("status") or "").upper()),
        "country": "CH",
        # Zefix is national; the cantonal registry office id is the closest to a
        # "court", but it is a numeric id, not a readable name -> leave None.
        "court": None,
        "url": firm.get("cantonalExcerptWeb") or FIRM_WEB,
    }


async def search_companies(name: str, auth: tuple[str, str], limit: int = 10) -> list[dict]:
    """Name search of the Swiss register. `auth` is (username, password)."""
    body = {"name": name, "languageKey": "en", "maxEntries": max(1, min(limit, 30))}
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.post(API, json=body, auth=auth)
        resp.raise_for_status()
        data = resp.json()
    # The API returns a bare list; tolerate a {"list": [...]} envelope too.
    firms = data if isinstance(data, list) else (data.get("list") or [])
    return [_normalise(f) for f in firms[:limit] if f.get("name")]
