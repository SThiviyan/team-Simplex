"""GLEIF (global LEI register) search provider.

GLEIF needs NO credentials and NO LLM — it calls the free public API directly,
so it always returns real results. It is the keyless default search provider.
"""

from app.integrations import gleif
from app.search.base import SearchProvider, SearchResult


def _score(query: str, name: str | None, rank: int) -> float:
    """Heuristic match strength so the orchestrator ranks exact names highest."""
    q = query.strip().lower()
    n = (name or "").strip().lower()
    if n == q:
        return 1.0
    if n.startswith(q):
        return 0.9
    base = 0.8 if q in n else 0.7
    return round(max(0.3, base - rank * 0.02), 4)


def _pick_jurisdiction(juris: str | None, country: str | None) -> str | None:
    """The state-level GLEIF jurisdiction (US-CA, CA-ON) when it refines the
    country; else the country. Keeps US/Canada state granularity that the
    registeredAs number is scoped to."""
    j = (juris or "").strip().upper()
    c = (country or "").strip().upper()
    if "-" in j and (not c or j.split("-")[0] == c):
        return j
    return c or (j or None)


def _snippet(e: dict) -> str:
    place = ", ".join(p for p in (e.get("city"), e.get("country")) if p)
    bits = [
        f"national registry number {e['registered_as']}" if e.get("registered_as") else None,
        f"LEI {e['lei']}" if e.get("lei") else None,
        e.get("entity_status"),
        place or None,
        f"jurisdiction {e['jurisdiction']}" if e.get("jurisdiction") else None,
    ]
    return " · ".join(b for b in bits if b)


class GleifSearchProvider(SearchProvider):
    name = "gleif"
    # Keyless and LLM-free, so it is always available.
    enabled = True
    # GLEIF is rate-limited (~1/s, global per-IP throttle). Under a big batch the
    # throttle queue can exceed the 12s fast-API default, so give it room — it is
    # fast per call, just paced. Without this the gather timeout cancels most
    # GLEIF calls in a batch and the GLEIF-only jurisdictions come back empty.
    search_timeout = 60.0

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            entities = await gleif.search_entities(query, limit=limit)
        except Exception:
            # Federated search treats provider failures as non-fatal.
            return []

        results: list[SearchResult] = []
        for i, e in enumerate(entities[:limit]):
            if not e.get("name"):
                continue
            results.append(
                SearchResult(
                    title=f"{e['name']} ({e['lei']})" if e.get("lei") else e["name"],
                    url=e.get("record_url"),
                    snippet=_snippet(e),
                    score=_score(query, e.get("name"), i),
                    source=self.name,
                    # Prefer GLEIF's state-level jurisdiction (US-CA, US-NY,
                    # CA-ON) over the bare country, since US/Canada register at
                    # state/province level and the registeredAs number is the
                    # STATE filing number. Falls back to the country otherwise.
                    jurisdiction=_pick_jurisdiction(e.get("jurisdiction"), e.get("country")),
                    # The NATIONAL registry number, not the LEI: an LEI is a
                    # cross-reference code, never the official registration
                    # number the output schema asks for. LEI lives in metadata.
                    registry_id=e.get("registered_as"),
                    register_name=e.get("name"),
                    last_update=e.get("last_update"),
                    address=e.get("address"),
                    organization_type=e.get("organization_type"),
                    status=e.get("entity_status"),
                    metadata={
                        k: v
                        for k, v in (
                            ("lei", e.get("lei")),
                            ("registration_authority", e.get("registration_authority")),
                        )
                        if v
                    },
                )
            )
        return results
