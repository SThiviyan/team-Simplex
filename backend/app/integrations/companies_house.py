"""Client for the UK Companies House Public Data API.

Companies House publishes the UK commercial register: every company registered
in England & Wales, Scotland and Northern Ireland, with the official company
number, status, type and registered-office address.

The API needs a free REST key (developer.company-information.service.gov.uk).
Authentication is HTTP Basic with the key as the username and a blank password.
Docs: https://developer-specs.company-information.service.gov.uk

Shared by both the company-registry MCP server (via the provider) and the
CompaniesHouseSearchProvider so the request/normalisation logic lives in one place.
"""

import httpx

from app.search.registry_format import infer_jurisdiction

API_BASE = "https://api.company-information.service.gov.uk"
# Public, human-readable company page (not the API host).
RECORD_WEB = "https://find-and-update.company-information.service.gov.uk/company"

# A few common Companies House `company_type` codes → readable labels. Anything
# not listed falls back to a de-slugified form of the raw code.
_TYPE_LABELS = {
    "ltd": "Private limited company",
    "plc": "Public limited company",
    "llp": "Limited liability partnership",
    "limited-partnership": "Limited partnership",
    "private-unlimited": "Private unlimited company",
    "old-public-company": "Old public company",
    "community-interest-company": "Community interest company",
    "charitable-incorporated-organisation": "Charitable incorporated organisation",
    "royal-charter": "Royal charter company",
    "uk-establishment": "UK establishment of an overseas company",
}


def _org_type(code: str | None) -> str | None:
    if not code:
        return None
    return _TYPE_LABELS.get(code, code.replace("-", " ").capitalize())


def _normalise(item: dict) -> dict:
    """Flatten one Companies House search item into a compact dict."""
    number = item.get("company_number")
    # Overseas companies / registered-overseas-entities carry their real country
    # in the structured address (e.g. "Germany") — the entry's own statement of
    # where it is, which must win over the UK register it was found in. A normal
    # UK company has no country (or "United Kingdom") here -> GB.
    addr_country = (item.get("address") or {}).get("country")
    return {
        "company_number": number,
        "name": item.get("title"),
        "status": item.get("company_status"),  # active / dissolved / liquidation / ...
        "address": item.get("address_snippet"),
        "date_of_creation": item.get("date_of_creation"),
        "organization_type": _org_type(item.get("company_type")),
        # ISO 3166-1 alpha-2 so the jurisdiction filter matches.
        "jurisdiction": infer_jurisdiction(addr_country) or "GB",
        "record_url": f"{RECORD_WEB}/{number}" if number else None,
    }


async def search_companies(name: str, api_key: str, limit: int = 10) -> list[dict]:
    """Search the UK register by company name. Returns normalised company dicts.

    Auth is HTTP Basic: the API key is the username, the password is blank.
    """
    params = {"q": name, "items_per_page": max(1, min(limit, 100)), "start_index": 0}
    async with httpx.AsyncClient(
        base_url=API_BASE, auth=(api_key, ""), timeout=20.0
    ) as client:
        resp = await client.get("/search/companies", params=params)
        resp.raise_for_status()
        return [_normalise(it) for it in resp.json().get("items", [])]


async def get_company(company_number: str, api_key: str) -> dict:
    """Fetch a single company profile by its registration number."""
    async with httpx.AsyncClient(
        base_url=API_BASE, auth=(api_key, ""), timeout=20.0
    ) as client:
        resp = await client.get(f"/company/{company_number.strip()}")
        resp.raise_for_status()
        data = resp.json()
        # /company/{number} returns the profile shape; reuse the snippet fields
        # we care about, mapping the profile's nested registered-office address.
        office = data.get("registered_office_address") or {}
        address = ", ".join(
            p
            for p in (
                office.get("address_line_1"),
                office.get("address_line_2"),
                office.get("locality"),
                office.get("postal_code"),
                office.get("country"),
            )
            if p
        )
        return {
            "company_number": data.get("company_number"),
            "name": data.get("company_name"),
            "status": data.get("company_status"),
            "address": address or None,
            "date_of_creation": data.get("date_of_creation"),
            "organization_type": _org_type(data.get("type")),
            "jurisdiction": "GB",
            "record_url": f"{RECORD_WEB}/{data.get('company_number')}",
        }
