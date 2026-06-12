"""Client for Finland's PRH / YTJ business register. Keyless open API.

Docs: https://avoindata.prh.fi/  (opendata-ytj-api v3)
"""

from app.integrations.http import shared_client

API = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"
# Official YTJ company page for a Y-tunnus — the citable public register view.
WEB = "https://tietopalvelu.ytj.fi/yritys"
_UA = "team-simplex-hackathon/1.0 (company search demo)"


def _current_name(names: list[dict]) -> str | None:
    """Pick the registered primary name (type "1"), else the first available."""
    primary = next((n.get("name") for n in names if n.get("type") == "1"), None)
    return primary or (names[0].get("name") if names else None)


def _rank(query: str, name: str | None) -> int:
    q, n = query.strip().lower(), (name or "").lower()
    if n == q:
        return 0
    if n.startswith(q):
        return 1
    if q in n:
        return 2
    return 3  # matched only via a historical name


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Finnish business register by name."""
    client = shared_client(
        "prh", timeout=25.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    )
    resp = await client.get(API, params={"name": name})
    resp.raise_for_status()
    data = resp.json()

    rows: list[dict] = []
    for co in data.get("companies", []):
        cur = _current_name(co.get("names") or [])
        if not cur:
            continue
        business_id = (co.get("businessId") or {}).get("value")  # Y-tunnus
        rows.append(
            {
                "number": business_id,
                "name": cur,
                "legal_form": None,
                "city": None,
                "status": None,
                "country": "FI",
                "court": None,
                "url": f"{WEB}/{business_id}" if business_id else None,
            }
        )
    # Surface companies whose current name matches the query first.
    rows.sort(key=lambda r: (_rank(name, r["name"]), len(r["name"])))
    return rows[:limit]
