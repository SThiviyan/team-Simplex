"""Jurisdiction-aware company resolver.

Given a name and (optionally) a jurisdiction, it:
  1. selects only the relevant providers — global ones always, national ones
     (e.g. the German register) only when their jurisdiction is requested;
  2. queries them in parallel;
  3. cross-references the results: clusters records that refer to the same
     company (normalised-name match), then ranks clusters by how many distinct
     sources corroborate them and how strongly each matched;
  4. returns the most-likely match plus the runner-up candidates and evidence.

Deterministic and keyless — no LLM in the path.
"""

import re
from difflib import SequenceMatcher

from app.search.base import SearchProvider, SearchResult

# Legal-form tokens stripped before comparing names across registers.
_LEGAL = re.compile(
    r"\b(gmbh|mbh|ag|se|kg|kgaa|ohg|ug|ev|eg|ltd|limited|inc|incorporated|llc|"
    r"plc|sa|sas|nv|bv|spa|srl|ltda|corp|corporation|company|co|holding|group|"
    r"the|und|and)\b",
    re.IGNORECASE,
)


# ~30 sitelinks ≈ a clearly prominent company; used to normalise prominence.
_PROMINENCE_SCALE = 30.0
# A cluster's name must match the query at least this closely for its
# prominence to count — so a big, loosely-named company can't bury a small
# exact-name match.
_PROMINENCE_NAME_GATE = 0.85


def _looks_like_abbreviation(query: str) -> bool:
    """A short, single-token, largely-uppercase query like UBS / IBM / BASF."""
    q = query.strip()
    if not q or " " in q or len(q) > 5:
        return False
    letters = [c for c in q if c.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    return upper_ratio >= 0.6 or len(q) <= 4


def _specificity(query: str) -> float:
    """0 = vague abbreviation (favour prominence); 1 = long/specific name
    (favour name matching)."""
    q = query.strip()
    if _looks_like_abbreviation(q):
        return 0.0
    words = q.split()
    score = 0.35
    score += min(len(q), 30) / 30 * 0.35  # longer name → more specific
    score += min(max(len(words) - 1, 0), 3) / 3 * 0.30  # more words → more specific
    return round(min(score, 1.0), 3)


def _normalise(title: str) -> str:
    n = title.lower()
    n = re.sub(r"\(.*?\)", " ", n)  # drop trailing "(LEI / QID / reg-no)"
    n = re.sub(r"[^a-z0-9 ]", " ", n)  # punctuation/diacritics-ish
    n = _LEGAL.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def select_providers(
    providers: list[SearchProvider], jurisdiction: str | None
) -> list[SearchProvider]:
    """Global providers always; national providers only on jurisdiction match."""
    juris = jurisdiction.upper() if jurisdiction else None
    chosen = []
    for p in providers:
        if p.jurisdictions is None:
            chosen.append(p)
        elif juris and juris in p.jurisdictions:
            chosen.append(p)
    return chosen


def _cluster(results: list[SearchResult]) -> list[dict]:
    """Group results that refer to the same company (fuzzy normalised name)."""
    clusters: list[dict] = []
    for r in results:
        key = _normalise(r.title)
        if not key:
            continue
        match = None
        for c in clusters:
            if key == c["key"] or SequenceMatcher(None, key, c["key"]).ratio() >= 0.9:
                match = c
                break
        if match is None:
            clusters.append({"key": key, "records": [r]})
        else:
            match["records"].append(r)
    return clusters


def _score_cluster(cluster: dict, n_selected: int, query: str, specificity: float) -> dict:
    records = cluster["records"]
    sources = sorted({r.source for r in records})
    max_score = max(r.score for r in records)
    qn = _normalise(query)
    # How closely the cluster's names match the actual query.
    name_sim = (
        max(SequenceMatcher(None, qn, _normalise(r.title)).ratio() for r in records) if qn else 0.0
    )
    corroboration = len(sources) / max(n_selected, 1)
    # Prominence (company "bigness") from the strongest signal across records.
    sitelinks = max((r.metadata.get("sitelinks", 0) for r in records), default=0)
    prominence = round(min(sitelinks / _PROMINENCE_SCALE, 1.0), 3)
    # Prominence only counts when the name actually matches the query well.
    # Otherwise a big but tangentially-named company would bury a small business
    # that IS an exact match — when no large business matches the name, small
    # ones should still rank (and surface) on name match alone.
    effective_prominence = prominence if name_sim >= _PROMINENCE_NAME_GATE else 0.0

    # Weight by query specificity: a specific/long name leans on name matching;
    # a vague abbreviation leans on prominence (rank bigger companies higher).
    w_name = 0.25 + 0.45 * specificity
    w_prom = 0.40 * (1.0 - specificity)
    w_corr = 0.20
    w_score = max(0.0, 1.0 - w_name - w_prom - w_corr)
    confidence = round(
        w_name * name_sim
        + w_prom * effective_prominence
        + w_corr * corroboration
        + w_score * max_score,
        3,
    )
    # Display the best-matching record's name (ties resolve to provider order).
    display = max(records, key=lambda r: r.score).title
    return {
        "name": display,
        "confidence": confidence,
        "name_match": round(name_sim, 3),
        "prominence": prominence,
        "sitelinks": sitelinks,
        "sources": sources,
        "source_count": len(sources),
        "evidence": [
            {"source": r.source, "title": r.title, "snippet": r.snippet, "url": r.url, "score": r.score}
            for r in sorted(records, key=lambda x: x.score, reverse=True)
        ],
    }


class CompanyResolver:
    def __init__(self, providers: list[SearchProvider]):
        self.providers = providers

    async def resolve(self, query: str, jurisdiction: str | None = None, limit: int = 10) -> dict:
        from app.search.base import normalize_country

        from app.config import settings
        from app.search.gather import run_tier, split_cost

        jurisdiction = normalize_country(jurisdiction)  # "UK" -> "GB" etc.
        selected = select_providers(self.providers, jurisdiction)

        # Bounded, cost-tiered gather (shared with the CSV/MCP path): the free
        # tier runs under gather_deadline; the slow/paid Apify tier runs only as a
        # fallback when no free source pinned a registry_id, under its own longer
        # deadline. A bare asyncio.gather here used to wait for the slowest source
        # with no cap and hung the live UI.
        free, premium = split_cost(selected)
        results = await run_tier(free, query, limit, settings.gather_deadline)
        if premium and not any(r.registry_id for r in results):
            results += await run_tier(
                premium, query, limit, settings.premium_gather_deadline
            )

        specificity = _specificity(query)
        clusters = [_score_cluster(c, len(selected), query, specificity) for c in _cluster(results)]
        # Rank by overall confidence (which already rewards corroboration),
        # then by source count as a tiebreak.
        clusters.sort(key=lambda c: (c["confidence"], c["source_count"]), reverse=True)

        return {
            "query": query,
            "jurisdiction": jurisdiction.upper() if jurisdiction else None,
            # How the query was read: abbreviation → favour big companies;
            # specific name → favour name matching.
            "query_kind": "abbreviation" if _looks_like_abbreviation(query) else "specific-name",
            "specificity": specificity,
            "sources_called": [p.name for p in selected],
            "sources_skipped": [p.name for p in self.providers if p not in selected],
            "most_likely": clusters[0] if clusters else None,
            "candidates": clusters[1:limit],
        }
