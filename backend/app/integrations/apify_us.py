"""US business-entity NAME search across state registries via the Apify
'pink_comic/us-business-entity-search' actor (key-gated, opt-in).

The US has no federal company register; this actor searches a set of state
Secretary-of-State registries by company name. Each hit carries the US state,
the state's entity id, and a source URL. Requires an Apify token + the global
APIFY_ENABLED flag.
"""

from app.integrations.apify import run_actor_get_items

ACTOR = "pink_comic~us-business-entity-search"
# Curated set of state registries searched by default (the actor's recommended,
# free-searchable states). Tunable via the `states` arg of search_companies.
DEFAULT_STATES = ["NY", "TX", "FL", "NJ", "CO", "WI", "OR"]
# Per-state result cap — kept small to bound free-plan compute.
_PER_STATE = 3


def _address(a: dict | None) -> str | None:
    # Compose from street/city only — a raw zip can carry junk (e.g. 'CANADA')
    # that would mislead jurisdiction inference downstream.
    if not isinstance(a, dict):
        return None
    return ", ".join(str(a[k]) for k in ("street", "city") if a.get(k)) or None


def to_row(item: dict) -> dict:
    """Map one actor dataset item to the shared register-row shape (+ metadata)."""
    state = item.get("state")
    addr = item.get("address") if isinstance(item.get("address"), dict) else {}
    return {
        "number": item.get("entityId"),
        "name": item.get("entityName"),
        "legal_form": item.get("entityType") or item.get("type"),
        "status": item.get("status"),
        "incorporation_date": item.get("registrationDate") or item.get("incorporationDate"),
        "country": "US",
        "court": f"{state} Secretary of State" if state else None,
        "city": addr.get("city"),
        "address": _address(addr),
        "url": item.get("sourceUrl"),
        "metadata": {"state": state, "search_query": item.get("searchQuery")},
    }


async def search_companies(
    query: str,
    token: str,
    *,
    limit: int = 10,
    states: list[str] | None = None,
    timeout: float = 90.0,
) -> list[dict]:
    """Name-search US state registries via the Apify actor; one row per hit."""
    if not query:
        return []
    items = await run_actor_get_items(
        ACTOR,
        {
            "searchQuery": query,
            "states": states or DEFAULT_STATES,
            "maxResults": _PER_STATE,
            "fetchDetails": False,
        },
        token,
        timeout=timeout,
    )
    rows = [to_row(it) for it in items if it.get("entityName")]
    return rows[:limit]
