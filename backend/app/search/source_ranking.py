"""Per-nation source hierarchy: which register to trust first for each country.

Resolving a company has a FOUNDATION source (the one that supplies the official
registry_id and anchors identity) and FILL sources (nice-to-have context for the
rest of the table). The order is data-driven (data/source_ranking.json), best /
freshest first, so:
  - the foundation is the highest-ranked source carrying a registry number;
  - Wikidata, which has no official registry_id, is always FILL-ONLY;
  - a national register outranks GLEIF outranks Wikidata.
Countries not pinned in the JSON fall back to the auto-derived order
[<national providers for that country>, gleif, wikidata].
"""

import json
from functools import cache
from pathlib import Path

_RANKING_FILE = Path(__file__).resolve().parents[1].parent / "data" / "source_ranking.json"

# Sources that never carry an official registry_id -> never a foundation.
FILL_ONLY_SOURCES = frozenset({"wikidata"})
# The cross-jurisdiction aggregator that sits between national registers and
# Wikidata when a country isn't pinned in the JSON.
_GLOBAL_FALLBACK = ("gleif", "wikidata")


@cache
def _ranking() -> dict[str, list[str]]:
    data = json.loads(_RANKING_FILE.read_text(encoding="utf-8"))
    return {k.upper(): v for k, v in data.items() if not k.startswith("_")}


@cache
def _national_by_country() -> dict[str, list[str]]:
    """country -> the national provider name(s) covering it (from the catalogue)."""
    from app.search.sources import all_providers

    out: dict[str, list[str]] = {}
    for p in all_providers():
        for cc in p.jurisdictions or ():
            out.setdefault(cc.upper(), []).append(p.name)
    return out


def ranked_sources(country: str | None) -> list[str]:
    """The ordered source list for a country (best first). Pinned JSON order if
    present, else auto-derived [<national>, gleif, wikidata]."""
    cc = (country or "").strip().upper().split("-")[0]
    pinned = _ranking().get(cc)
    if pinned:
        return pinned
    national = _national_by_country().get(cc, [])
    return [*national, *_GLOBAL_FALLBACK]


def rank_of(country: str | None, source: str | None) -> int:
    """Priority of `source` for `country` — lower is better. Listed sources keep
    their index; unlisted sources sort AFTER all listed ones, with national
    registers ahead of GLEIF ahead of Wikidata ahead of anything unknown."""
    order = ranked_sources(country)
    if source in order:
        return order.index(source)
    base = len(order)
    if source in FILL_ONLY_SOURCES:
        return base + 30
    if source == "gleif":
        return base + 20
    if source is None:
        return base + 40
    # An unlisted national register still beats GLEIF/Wikidata fallback.
    return base + 10


def is_foundation_source(source: str | None) -> bool:
    """True if this source may anchor the identity (carry the registry_id)."""
    return bool(source) and source not in FILL_ONLY_SOURCES


def order_records(records: list[dict], country: str | None) -> list[dict]:
    """Records sorted by the country's source hierarchy, best source first."""
    return sorted(records, key=lambda r: rank_of(country, r.get("provider")))
