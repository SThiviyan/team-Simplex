"""Client for Denmark's CVR (Det Centrale Virksomhedsregister) via cvrapi.dk.

Free, keyless API (a descriptive User-Agent is required). Returns the single
best-matching company for a name search.
Docs: https://cvrapi.dk/documentation
"""

import httpx

API = "https://cvrapi.dk/api"
WEB = "https://datacvr.virk.dk/enhed/virksomhed"
# cvrapi.dk blocks generic/empty User-Agents — identify the app.
_UA = "team-simplex-hackathon/1.0 (Sinpex Hackathon company search demo)"


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Danish CVR register by name (returns the best match)."""
    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": _UA}) as client:
        resp = await client.get(API, params={"search": name, "country": "dk"})
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        d = resp.json()

    if not isinstance(d, dict) or d.get("error"):
        return []
    vat = d.get("vat")
    return [
        {
            "number": str(vat) if vat else None,
            "name": d.get("name"),
            "legal_form": d.get("companydesc"),
            "city": d.get("city"),
            "status": "ophørt" if d.get("enddate") else "aktiv",
            "country": "DK",
            "url": f"{WEB}/{vat}" if vat else None,
        }
    ]
