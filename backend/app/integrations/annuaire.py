"""Client for France's Annuaire des Entreprises (API Recherche d'entreprises).

Free, keyless open API run by DINUM.
Docs: https://recherche-entreprises.api.gouv.fr/docs
"""

import httpx

API = "https://recherche-entreprises.api.gouv.fr/search"
WEB = "https://annuaire-entreprises.data.gouv.fr/entreprise"
_UA = "team-simplex-hackathon/1.0 (company search demo)"


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the French business directory by name."""
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.get(
            API, params={"q": name, "per_page": max(1, min(limit, 25)), "page": 1}
        )
        resp.raise_for_status()
        data = resp.json()

    out: list[dict] = []
    for e in data.get("results", []):
        siege = e.get("siege") or {}
        siren = e.get("siren")
        city = siege.get("libelle_commune") or siege.get("code_postal")
        etat = e.get("etat_administratif")
        out.append(
            {
                "number": siren,
                "name": e.get("nom_complet") or e.get("nom_raison_sociale"),
                "legal_form": None,  # API only gives a numeric nature_juridique code
                "city": city,
                "status": {"A": "active", "C": "ceased"}.get(etat),
                "incorporation_date": e.get("date_creation"),
                "country": "FR",
                "url": f"{WEB}/{siren}" if siren else None,
            }
        )
    return out
