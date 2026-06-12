"""Client for Ireland's Companies Registration Office (CRO) — KEYLESS.

The CRO's own CWS API requires a registered key, so instead we query the
official CRO "Company Records" open dataset published on data.gov.ie via its
keyless CKAN `datastore_search_sql` endpoint (ILIKE name match).

COVERAGE CAVEAT: the open dataset is a limited extract (not the full ~1.5M
register — the CRO gates the complete data behind its keyed API). Irish
companies are still covered well by the global GLEIF + Wikidata providers,
which always run for IE queries; this adds whatever the open CRO dataset holds.
"""

import httpx

CKAN_SQL = "https://data.gov.ie/api/3/action/datastore_search_sql"
# data.gov.ie "Company Records" (CRO) datastore-active resource.
RESOURCE_ID = "3fef41bc-b8f4-4b10-8434-ce51c29b1bba"
_UA = "team-simplex-hackathon/1.0 (company search demo)"


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search Ireland's CRO open dataset by name (keyless, via data.gov.ie)."""
    safe = name.replace("'", "''")  # escape for the SQL string literal
    n = max(1, min(limit, 50))
    sql = (
        f'SELECT company_num, company_name, company_status, company_type, '
        f'company_address_1, company_address_4 FROM "{RESOURCE_ID}" '
        f"WHERE company_name ILIKE '%{safe}%' LIMIT {n}"
    )
    async with httpx.AsyncClient(
        timeout=25.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.get(CKAN_SQL, params={"sql": sql})
        if resp.status_code != 200:
            return []
        data = resp.json()

    if not data.get("success"):
        return []
    out: list[dict] = []
    for e in data.get("result", {}).get("records", []):
        num = e.get("company_num")
        out.append(
            {
                "number": str(num) if num is not None else None,
                "name": e.get("company_name"),
                "legal_form": e.get("company_type"),
                "city": e.get("company_address_4") or e.get("company_address_1"),
                "status": e.get("company_status"),
                "country": "IE",
                "url": None,
            }
        )
    return out
