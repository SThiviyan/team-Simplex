"""Corroboration / "fame" via graph consolidation (technique ported from
worldbank/FuzzyAI).

The gather layer returns one record per provider hit, so a well-known company
shows up many times (GLEIF + Wikidata + Handelsregister + …). FuzzyAI groups
duplicates by building a similarity graph and taking its **connected
components**, which captures *transitive* duplicates: if A≈B and B≈C, then
{A, B, C} are one entity even when A and C don't directly match (e.g.
"Siemens" ≈ "Siemens AG" ≈ "Siemens Aktiengesellschaft").

Each component is merged into one most-complete record carrying a **fame**
signal (how many sources found it). :func:`corroboration_boost` then lifts the
confidence of well-attested entities toward 1.0, so a mainstream result wins even
when the input differs from the registered name. A single-source entity is never
boosted — corroboration only ever *adds* evidence.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
from rapidfuzz import fuzz

from app.matching.company_matcher import (
    CONFIDENCE_FIELD,
    JURISDICTION_FIELD,
    NAME_FIELD,
)
from app.matching.conflict_resolution import Verifier, cross_reference
from app.matching.token_weights import discriminative_core

# Core-name similarity (0..100) above which two records are the same entity.
_SAME_ENTITY_SIM = 88

# Attributes whose presence makes a registry record "complete".
_COMPLETENESS_FIELDS = (
    "registry_id",
    "registry_court",
    "address",
    "organization_type",
    "status",
    "last_update",
    "incorporation_date",
    "source",
)

# How much corroboration can lift confidence toward 1.0, and how fast it
# saturates with each additional distinct source.
_BOOST_WEIGHT = 0.3
_SOURCE_DECAY = 0.55  # factor = 1 - DECAY**extra_sources: 0, .45, .70, .83, …


def completeness(record: dict[str, Any]) -> float:
    """Share of the key registry attributes that are populated, in [0, 1]."""
    present = sum(1 for f in _COMPLETENESS_FIELDS if record.get(f))
    return round(present / len(_COMPLETENESS_FIELDS), 4)


def _same_entity(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Do two records refer to the same entity? (edge predicate for the graph)."""
    ja = (a.get(JURISDICTION_FIELD) or "").strip().upper()
    jb = (b.get(JURISDICTION_FIELD) or "").strip().upper()
    if ja and jb and ja != jb:
        return False  # different countries → different entities
    # Same official registry id is decisive.
    ra = (a.get("registry_id") or "").strip().upper()
    rb = (b.get("registry_id") or "").strip().upper()
    if ra and rb and ra == rb:
        return True
    ca = discriminative_core(a.get(NAME_FIELD))
    cb = discriminative_core(b.get(NAME_FIELD))
    if not ca or not cb:
        return False
    return fuzz.token_sort_ratio(ca, cb) >= _SAME_ENTITY_SIM


def _merge_group(
    group: list[dict[str, Any]], *, verifier: Verifier | None = None
) -> dict[str, Any]:
    """Merge same-entity records into the most complete one.

    Two passes: first fill empty fields from any record in the group, then
    cross-reference the fields where records *disagree* (e.g. two different
    addresses) and write the best-supported value — rather than letting the
    most-complete record's value win by default. See ``conflict_resolution``.
    """
    base = max(
        group,
        key=lambda r: (completeness(r), float(r.get(CONFIDENCE_FIELD) or 0.0)),
    )
    merged = dict(base)
    for record in group:
        for field in _COMPLETENESS_FIELDS:
            if not merged.get(field) and record.get(field):
                merged[field] = record[field]
    merged[CONFIDENCE_FIELD] = max(float(r.get(CONFIDENCE_FIELD) or 0.0) for r in group)
    # Resolve conflicting (non-empty but differing) fields by cross-referencing
    # the gathered evidence; writes the winning value into `merged` in place.
    cross_reference(group, merged, verifier=verifier)
    return merged


def corroborate(
    records: list[dict[str, Any]], *, verifier: Verifier | None = None
) -> list[dict[str, Any]]:
    """Cluster duplicate records (graph connected components); attach fame.

    Each returned record gains ``_fame`` (mentions), ``_provider_count``
    (distinct sources), ``_providers`` (their names), and ``_completeness``.
    Conflicting attribute values within a cluster are cross-referenced and the
    best-supported value is kept (with a ``_conflicts`` trail); pass ``verifier``
    to add an external reverse lookup as a tie-breaker.
    """
    recs = [r for r in records if r.get(NAME_FIELD)]
    graph: nx.Graph = nx.Graph()
    graph.add_nodes_from(range(len(recs)))
    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            if _same_entity(recs[i], recs[j]):
                graph.add_edge(i, j)

    merged_records: list[dict[str, Any]] = []
    for component in nx.connected_components(graph):
        group = [recs[k] for k in component]
        merged = _merge_group(group, verifier=verifier)
        providers = sorted({r.get("provider") for r in group if r.get("provider")})
        merged["_fame"] = len(group)
        merged["_providers"] = providers
        merged["_provider_count"] = len(providers) or 1
        merged["_completeness"] = completeness(merged)
        merged_records.append(merged)
    return merged_records


def corroboration_boost(confidence: float, provider_count: int) -> float:
    """Lift ``confidence`` toward 1.0 based on how many distinct sources agree.

    One source → unchanged. Each extra source adds a saturating fraction of the
    remaining headroom, so fame can boost a well-attested mainstream entity
    without ever overriding a clearly better name match.
    """
    extra = max(int(provider_count) - 1, 0)
    if extra == 0:
        return round(min(max(confidence, 0.0), 1.0), 4)
    fame_factor = 1.0 - (_SOURCE_DECAY**extra)
    boosted = confidence + _BOOST_WEIGHT * fame_factor * (1.0 - confidence)
    return round(min(max(boosted, 0.0), 1.0), 4)
