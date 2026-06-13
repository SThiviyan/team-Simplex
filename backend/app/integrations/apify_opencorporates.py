"""Global company search via the Apify 'ryanclinton/opencorporates-search' actor
("Corporate Decision Intelligence: KYC, Compliance, Supplier Risk"). Name search
across jurisdictions (OpenCorporates-backed). Opt-in, premium.
"""

from app.integrations.apify import run_actor_get_items

ACTOR = "ryanclinton~opencorporates-search"


def to_row(it: dict) -> dict:
    juris = (it.get("jurisdiction") or (it.get("jurisdictionCode") or "")[:2]).upper() or None
    risk = it.get("risk") if isinstance(it.get("risk"), dict) else {}
    return {
        "number": it.get("companyNumber"),
        "name": it.get("companyName"),
        "legal_form": it.get("companyType") or it.get("entityType"),
        "city": None,
        "status": it.get("status"),
        "incorporation_date": it.get("incorporationDate"),
        "country": juris,
        "court": None,
        "address": it.get("registeredAddress"),
        "url": it.get("registryUrl") or it.get("opencorporatesUrl"),
        "snippet": " · ".join(
            str(x)
            for x in (it.get("jurisdiction"), it.get("companyType"), it.get("status"), risk.get("level"))
            if x
        ),
        "metadata": {
            "source": it.get("source"),
            "risk": it.get("risk"),
            "data_quality": it.get("dataQuality"),
        },
    }


async def search_companies(query: str, token: str, *, limit: int = 10, timeout: float = 90.0) -> list[dict]:
    if not query:
        return []
    items = await run_actor_get_items(
        ACTOR, {"query": query, "maxResults": max(1, min(limit, 10))}, token, timeout=timeout
    )
    return [to_row(it) for it in items if it.get("companyName")][:limit]
