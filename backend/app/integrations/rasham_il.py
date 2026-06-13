"""Client for Israel's Companies Registrar open data (data.gov.il).

Free, keyless CKAN datastore published by the Israeli Ministry of Justice
Companies Registrar (רשם החברות). Full-text search by name (Hebrew or English).

Shared by the company-registry MCP server (via the provider) and the
RashamIlSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

API = "https://data.gov.il/api/3/action/datastore_search"
RESOURCE_ID = "f004176c-b85f-4542-8901-7b3176f9a054"
_UA = "team-simplex-hackathon/1.0 (company search demo)"

# Hebrew datastore field keys.
_NUM = "מספר חברה"
_NAME_HE = "שם חברה"
_NAME_EN = "שם באנגלית"
_TYPE = "סוג תאגיד"
_STATUS = "סטטוס חברה"
_INC = "תאריך התאגדות"
_CITY = "שם עיר"
_STREET = "שם רחוב"
_HOUSE = "מספר בית"
_POSTAL = "מיקוד"


def _status(s: str | None) -> str | None:
    if not s:
        return None
    if "פעיל" in s:  # active
        return "active"
    if "מחוסל" in s or "מחיק" in s or "נמחק" in s:  # liquidated / struck off / deleted
        return "dissolved"
    return None  # avoid snake-casing arbitrary Hebrew


def _date(s: str | None) -> str | None:
    """Israeli dates are dd/mm/yyyy -> ISO yyyy-mm-dd."""
    if s and s.count("/") == 2:
        day, month, year = s.split("/")
        return f"{year}-{month}-{day}"
    return s


def _address(r: dict) -> str | None:
    line = " ".join(str(p) for p in (r.get(_STREET), r.get(_HOUSE)) if p)
    tail = " ".join(str(p) for p in (r.get(_POSTAL), r.get(_CITY)) if p)
    return ", ".join(p for p in (line or None, tail or None) if p) or None


def _normalise(r: dict) -> dict:
    num = r.get(_NUM)
    return {
        "number": str(num) if num else None,  # company number
        "name": r.get(_NAME_EN) or r.get(_NAME_HE),
        "legal_form": r.get(_TYPE),  # native Hebrew legal form
        "city": r.get(_CITY),
        "address": _address(r),
        "status": _status(r.get(_STATUS)),
        "incorporation_date": _date(r.get(_INC)),
        "country": "IL",
        "court": None,
        "url": None,
    }


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Israeli companies register by name (Hebrew or English)."""
    params = {"resource_id": RESOURCE_ID, "q": name, "limit": max(1, min(limit, 50))}
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.get(API, params=params)
        resp.raise_for_status()
        records = resp.json().get("result", {}).get("records", [])
        return [_normalise(r) for r in records[:limit]]
