"""Wikidata (SPARQL) search provider.

Keyless and LLM-free: queries the public Wikidata SPARQL endpoint directly via
the mwapi EntitySearch service, so it always returns real results.
"""

from app.integrations import wikidata
from app.search.base import SearchProvider, SearchResult


def _score(query: str, label: str | None, rank: int) -> float:
    q = query.strip().lower()
    n = (label or "").strip().lower()
    if n == q:
        return 1.0
    if n.startswith(q):
        return 0.9
    base = 0.8 if q in n else 0.7
    return round(max(0.3, base - rank * 0.03), 4)


class WikidataSearchProvider(SearchProvider):
    name = "wikidata"
    # Public SPARQL endpoint, no credentials needed.
    enabled = True
    tier = "global"

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        try:
            entities = await wikidata.search_entities(query, limit=limit)
        except Exception:
            # Federated search treats provider failures as non-fatal.
            return []

        results: list[SearchResult] = []
        for i, e in enumerate(entities[:limit]):
            label = e.get("label")
            if not label:
                continue
            results.append(
                SearchResult(
                    title=f"{label} ({e['qid']})" if e.get("qid") else label,
                    url=e.get("url"),
                    snippet=e.get("description") or "Wikidata entity",
                    score=_score(query, label, i),
                    source=self.name,
                    jurisdiction=e.get("jurisdiction"),
                    # The official registration number when Wikidata has one (via
                    # OpenCorporates); NOT the QID — that stays in the title/url.
                    registry_id=e.get("registration_number"),
                    register_name=label,
                    metadata={"sitelinks": e.get("sitelinks", 0)},
                )
            )
        return results
