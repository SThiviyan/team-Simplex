"""Client for Denmark's CVR (Det Centrale Virksomhedsregister) via cvrapi.dk.

Free, keyless API (a descriptive User-Agent is required). Returns the single
best-matching company for a name search.
Docs: https://cvrapi.dk/documentation
"""

from app.integrations.http import shared_client

API = "https://cvrapi.dk/api"
WEB = "https://datacvr.virk.dk/enhed/virksomhed"
# cvrapi.dk blocks generic/empty User-Agents — identify the app.
_UA = "team-simplex-hackathon/1.0 (Sinpex Hackathon company search demo)"


def _iso_date(raw: str | None) -> str | None:
    """cvrapi reports dates as 'DD/MM - YYYY'; convert to ISO when parseable."""
    if not raw:
        return None
    try:
        daymonth, year = raw.split(" - ")
        day, month = daymonth.split("/")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except (ValueError, AttributeError):
        return raw


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Danish CVR register by name (returns the best match)."""
    client = shared_client("cvr", timeout=20.0, headers={"User-Agent": _UA})
    resp = await client.get(API, params={"search": name, "country": "dk"})
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    d = resp.json()

    if not isinstance(d, dict) or d.get("error"):
        return []
    vat = d.get("vat")
    full_address = ", ".join(
        part for part in (d.get("address"), d.get("zipcode"), d.get("city"), "DK") if part
    )
    return [
        {
            "number": str(vat) if vat else None,
            "name": d.get("name"),
            "legal_form": d.get("companydesc"),
            "city": d.get("city"),
            "status": "ophørt" if d.get("enddate") else "aktiv",
            "country": "DK",
            "url": f"{WEB}/{vat}" if vat else None,
            "address": full_address or None,
            "incorporation_date": _iso_date(d.get("startdate")),
            "industry_code": str(d["industrycode"]) if d.get("industrycode") else None,
            "industry": d.get("industrydesc"),
        }
    ]
