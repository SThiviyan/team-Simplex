"""Cross-reference conflicting fields among same-entity records.

The corroboration layer (``corroboration.py``) merges records that refer to the
same entity (same registry id / near-identical name, same country). Those
duplicates frequently DISAGREE on attribute values — most often two sources give
the same company a different registered ``address``, but ``status`` /
``registry_court`` / dates can clash too. The naive merge keeps the most-complete
record's value and silently drops the alternative, which can write the wrong
address to the output.

This module instead RESOLVES each conflict by cross-referencing the evidence we
already gathered, scoring every candidate value by:

1. **Source authority** — official national registers outrank the global LEI
   source (GLEIF), which outranks the crowd aggregator (Wikidata). The register
   is the authoritative source for a company's own registered details.
2. **Address-component corroboration** — for addresses, how many of the other
   gathered records mention this value's postal code / city tokens. This is the
   offline analogue of "reverse-searching the address": a value whose components
   recur across independent records has more supporting evidence.
3. **Multi-source agreement** — how many distinct sources reported the value.
4. **Recency** — prefer the value from the most recently updated record.

The best-supported value is written back to the merged record (so it flows to the
output), and a structured ``_conflicts`` trail records the alternatives and why
one won.

An optional ``verifier`` callable can perform a genuine external reverse lookup
(e.g. geocode the address) to break ties the local evidence can't settle; by
default resolution is fully deterministic and offline.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

# A verifier is given the conflicting field and the tied candidate values and
# returns the value it can support (or None if it can't decide).
Verifier = Callable[[str, list[str]], str | None]

# Source authority. Unknown providers default to the national-register tier —
# every provider in app.search.sources except the two below IS a register.
_AUTHORITY: dict[str, int] = {
    "gleif": 2,  # official global identifier (LEI), but not the entity's register
    "wikidata": 1,  # crowd-sourced aggregator
}
_DEFAULT_AUTHORITY = 3  # official national register

# Attribute fields worth resolving when same-entity records disagree. Identity
# fields (name, registry_id) are what *defined* the merge, so they aren't
# re-litigated here.
_RESOLVE_FIELDS = (
    "address",
    "organization_type",
    "status",
    "registry_court",
    "last_update",
    "incorporation_date",
)

_POSTAL_RE = re.compile(r"\b\d{4,6}\b")
_TOKEN_RE = re.compile(r"[^0-9a-zäöüßéèçñ]+", re.IGNORECASE)

# Scoring weights — authority dominates (an official register beats an aggregator
# regardless of vote count, since it is the source of truth for registry data);
# component corroboration and votes break ties between equally authoritative
# sources; recency is the final tie-breaker.
_W_AUTHORITY = 1000.0
_W_COMPONENT = 50.0
_W_VOTES = 10.0
_W_RECENCY = 1e-6


def authority(provider: str | None) -> int:
    """Trust tier for a provider (higher = more authoritative)."""
    return _AUTHORITY.get((provider or "").strip().lower(), _DEFAULT_AUTHORITY)


def _norm(value: Any) -> str:
    """Normalize a value for equality comparison (whitespace + case)."""
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _address_components(addr: str) -> tuple[set[str], set[str]]:
    """Split an address into (postal codes, significant word tokens)."""
    low = addr.lower()
    postals = set(_POSTAL_RE.findall(low))
    tokens = {t for t in _TOKEN_RE.split(low) if len(t) > 3}
    return postals, tokens


def _recency(record: dict[str, Any]) -> float:
    """Ordinal of the record's last_update date, or 0 when absent/unparseable."""
    raw = record.get("last_update")
    try:
        return float(date.fromisoformat(str(raw)[:10]).toordinal())
    except (ValueError, TypeError):
        return 0.0


def _component_support(value: str, records: list[dict[str, Any]]) -> int:
    """How many gathered records corroborate an address value's components.

    Cross-references the candidate address against every record's address: each
    record that shares a postal code or a city token counts as supporting
    evidence. The offline stand-in for reverse-searching the address.
    """
    postals, tokens = _address_components(value)
    if not postals and not tokens:
        return 0
    support = 0
    for r in records:
        other = r.get("address")
        if not other:
            continue
        op, ot = _address_components(str(other))
        if (postals & op) or (tokens & ot):
            support += 1
    return support


def resolve_field(
    field: str,
    records: list[dict[str, Any]],
    *,
    verifier: Verifier | None = None,
) -> dict[str, Any] | None:
    """Pick the best-supported value for ``field`` across same-entity records.

    Returns a resolution dict ``{field, chosen, alternatives, reason}`` when the
    records disagree (≥2 distinct non-empty values), else ``None``.
    """
    # Bucket records by their normalized value for this field.
    by_value: dict[str, dict[str, Any]] = {}
    for r in records:
        raw = r.get(field)
        if not raw:
            continue
        slot = by_value.setdefault(
            _norm(raw), {"value": raw, "providers": set(), "recency": 0.0}
        )
        provider = r.get("provider")
        if provider:
            slot["providers"].add(provider)
        slot["recency"] = max(slot["recency"], _recency(r))

    if len(by_value) <= 1:
        return None  # all sources agree (or only one had a value) — no conflict

    scored: list[dict[str, Any]] = []
    for slot in by_value.values():
        providers = slot["providers"]
        auth = max((authority(p) for p in providers), default=_DEFAULT_AUTHORITY)
        votes = len(providers) or 1
        component = _component_support(str(slot["value"]), records) if field == "address" else 0
        score = (
            _W_AUTHORITY * auth
            + _W_COMPONENT * component
            + _W_VOTES * votes
            + _W_RECENCY * slot["recency"]
        )
        scored.append(
            {
                "value": slot["value"],
                "providers": sorted(providers),
                "authority": auth,
                "votes": votes,
                "component_support": component,
                "score": round(score, 6),
            }
        )
    scored.sort(key=lambda s: s["score"], reverse=True)

    top = scored[0]
    # When the local evidence can't separate the leaders, optionally let an
    # external reverse lookup decide.
    tied = [s for s in scored if abs(s["score"] - top["score"]) < 1e-9]
    if len(tied) > 1 and verifier is not None:
        picked = verifier(field, [s["value"] for s in tied])
        if picked:
            for s in tied:
                if _norm(s["value"]) == _norm(picked):
                    top = s
                    break

    return {
        "field": field,
        "chosen": top["value"],
        "alternatives": scored,
        "reason": _reason(field, top, scored),
    }


def _reason(field: str, top: dict[str, Any], scored: list[dict[str, Any]]) -> str:
    """A short human explanation of why ``top`` won."""
    runner = next((s for s in scored if s["value"] != top["value"]), None)
    bits: list[str] = []
    if runner and top["authority"] > runner["authority"]:
        src = ", ".join(top["providers"]) or "register"
        bits.append(f"higher-authority source ({src})")
    if field == "address" and top["component_support"] > (
        runner["component_support"] if runner else 0
    ):
        bits.append(f"address corroborated by {top['component_support']} records")
    if top["votes"] > 1 and (not runner or top["votes"] > runner["votes"]):
        bits.append(f"{top['votes']} sources agree")
    if not bits:
        bits.append("best available evidence")
    return "; ".join(bits)


def cross_reference(
    group: list[dict[str, Any]],
    merged: dict[str, Any],
    *,
    verifier: Verifier | None = None,
) -> dict[str, Any]:
    """Resolve every conflicting field in ``group`` and write the winners into
    ``merged`` in place. Attaches a ``_conflicts`` trail when any field clashed.
    """
    if len(group) < 2:
        return merged
    conflicts: list[dict[str, Any]] = []
    for field in _RESOLVE_FIELDS:
        resolution = resolve_field(field, group, verifier=verifier)
        if resolution is None:
            continue
        merged[field] = resolution["chosen"]
        conflicts.append(resolution)
    if conflicts:
        merged["_conflicts"] = conflicts
    return merged
