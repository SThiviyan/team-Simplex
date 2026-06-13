"""France — companies NAME search & SIREN enrich via the Apify
'corent1robert/recherche-entreprises-scraper' actor (wraps the public
recherche-entreprises.api.gouv.fr). Opt-in.
"""

from urllib.parse import quote

from app.integrations.apify import run_actor_get_items

ACTOR = "corent1robert~recherche-entreprises-scraper"
_API = "https://recherche-entreprises.api.gouv.fr/search?q="


def to_row(it: dict) -> dict:
    siren = it.get("siren")
    return {
        "number": siren,
        "name": it.get("nom_complet") or it.get("nom_raison_sociale"),
        "legal_form": it.get("nature_juridique"),
        "city": it.get("ville"),
        "status": None if it.get("etat_administratif") == "A" else "dissolved",
        "incorporation_date": it.get("date_creation"),
        "country": "FR",
        "court": None,
        "address": it.get("adresse"),
        "url": (
            f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}" if siren else None
        ),
        "snippet": " · ".join(
            str(x)
            for x in (it.get("libelle_activite_principale"), it.get("ville"), it.get("departement_nom"))
            if x
        ),
        "metadata": {
            "siret_siege": it.get("siret_siege"),
            "activite": it.get("activite_principale"),
            "region": it.get("region_nom"),
        },
    }


async def search_companies(query: str, token: str, *, limit: int = 10, timeout: float = 90.0) -> list[dict]:
    if not query:
        return []
    n = max(1, min(limit, 10))
    url = f"{_API}{quote(query)}&per_page={n}"
    items = await run_actor_get_items(
        ACTOR,
        {"mode": "searchUrl", "searchUrls": [url], "maxResults": n,
         "requireDirigeant": False, "requireDirigeantPhysique": False},
        token,
        timeout=timeout,
    )
    return [to_row(it) for it in items if it.get("nom_complet") or it.get("nom_raison_sociale")][:limit]
