"""Deterministic, explainable confidence scoring — the ONE place the final
confidence_flag and the [0, 1] confidence number are decided.

Why this exists: the number used to be a grab-bag of incompatible subjective
values (the Layer-1 LLM's self-rated confidence, the matcher's fuzzy blend, a
second LLM confidence from the semantic filter, a hardcoded 0.9 fast-path
value), glued together and then capped by the flag. Two equally-evidenced rows
could end up showing very different percentages for no traceable reason.

Here the number is instead a SUM OF NAMED EVIDENCE SIGNALS, each contributing a
fixed, documented amount, and the flag is derived from the same signals — so the
two always tell a consistent story and every point is attributable. The
subjective agent/matcher numbers still drive the live trajectory display (the
"how we got here"), but the final scored value is recomputed here.
"""

import re
from dataclasses import dataclass
from functools import cache

from app.pipeline.models import ExtractionResult, QueryRow
from app.search.base import normalize_country

# confidence_flag vocabulary (one per row).
FLAG_VERIFIED = "verified"      # registry-backed ID, or corroborated by >=2 sources
FLAG_PROBABLE = "probable"      # ID present but single, non-register source
FLAG_AMBIGUOUS = "ambiguous"    # several candidates, none chosen
FLAG_NOT_FOUND = "not_found"    # honest blank (no ID in any source)
FLAG_ERROR = "error"            # the pipeline failed, not the registry

_ERROR_PREFIXES = ("layer1_error", "pipeline_error")

# --- evidence weights -------------------------------------------------------
# Each is a fixed, documented contribution so the final number reads as a sum of
# named signals. They sum to 1.0 for a fully-evidenced row (an official number,
# register-backed, independently corroborated, jurisdiction-aligned, full Tier A
# coverage), and never push a row above what its flag allows (see _CAPS).
W_BASE_ID = 0.50            # an official registry number was found at all
W_REGISTER_BACKED = 0.20    # the number is backed by the NATIONAL register
W_CORROBORATION = 0.12      # >=2 independent sources carry the same number
W_JURISDICTION = 0.10       # confirmed jurisdiction matches the request
W_JURISDICTION_NONE = 0.05  # no jurisdiction was requested -> half credit
W_COVERAGE = 0.08           # scaled by Tier A datapoint coverage

CONTRADICTION_PENALTY = 0.10      # per cross-source contradiction
CONTRADICTION_PENALTY_CAP = 0.30  # ... capped here

# Deterministic values for rows that assert no single positive identity. There
# is no entity to be confident about, so the number reflects that directly
# rather than carrying a leftover subjective value.
CONF_AMBIGUOUS = 0.30
CONF_NOT_FOUND = 0.0
CONF_ERROR = 0.0

# The four core Tier A fields that drive the coverage signal (kept stable: these
# are the always-expected datapoints, independent of the wider Tier A set).
_COVERAGE_FIELDS = ("registered_address", "incorporation_date", "organization_type", "status")

# A wrong-but-confident answer scores worse than a blank one, so the flag still
# bounds the number from above. Caps only ever lower.
_CAPS = {
    FLAG_ERROR: 0.0,
    FLAG_AMBIGUOUS: 0.4,
    FLAG_NOT_FOUND: 0.6,
    FLAG_PROBABLE: 0.85,
    FLAG_VERIFIED: 1.0,
}

_ALNUM = re.compile(r"[^a-z0-9]")


def _ids_match(a: str | None, b: str | None) -> bool:
    na = _ALNUM.sub("", (a or "").lower())
    nb = _ALNUM.sub("", (b or "").lower())
    return bool(na) and bool(nb) and (na == nb or na.lstrip("0") == nb.lstrip("0"))


@cache
def _registry_provider_names() -> frozenset[str]:
    """Names of the jurisdiction-scoped national registers (derived from the
    provider catalogue, never hardcoded)."""
    from app.search.sources import all_providers

    return frozenset(p.name for p in all_providers() if p.jurisdictions is not None)


def _country(code: str | None) -> str | None:
    if not code or not code.strip():
        return None
    head = code.strip().upper().split("-")[0]
    return normalize_country(head) or head


@dataclass
class Component:
    """One named contribution to the score (positive or negative)."""

    label: str
    points: float
    detail: str


@dataclass
class Confidence:
    flag: str
    value: float
    components: list[Component]

    def as_event(self) -> list[dict]:
        """Compact, JSON-loggable breakdown for the event log / frontend."""
        return [
            {"label": c.label, "points": round(c.points, 3), "detail": c.detail}
            for c in self.components
        ]


def _providers_with_id(result: ExtractionResult, matched: list[dict]) -> set[str]:
    found = {
        r.get("provider")
        for r in matched
        if _ids_match(result.registry_id, r.get("registry_id"))
    }
    found.discard(None)
    return found


def confidence_flag(result: ExtractionResult, matched: list[dict]) -> str:
    """The calibration flag for a row, derived purely from observable evidence.

    error  -> the pipeline failed; ambiguous -> several candidates, none chosen;
    not_found -> no official number anywhere; verified -> the number is backed by
    a national register OR >=2 independent sources agree on it; else probable.
    """
    reason = result.no_match_reason or ""
    if reason.startswith(_ERROR_PREFIXES):
        return FLAG_ERROR
    if reason.startswith("ambiguous_candidates"):
        return FLAG_AMBIGUOUS
    if not result.registry_id:
        return FLAG_NOT_FOUND
    providers = _providers_with_id(result, matched)
    if providers & _registry_provider_names():
        return FLAG_VERIFIED
    if len(providers) >= 2:
        return FLAG_VERIFIED
    return FLAG_PROBABLE


def compute_confidence(
    result: ExtractionResult,
    matched: list[dict],
    contradictions: list[dict],
    query: QueryRow,
) -> Confidence:
    """Derive (flag, confidence, breakdown) from observable evidence signals.

    The returned value is a sum of the named components, clamped to [0, 1] and to
    the band the flag allows. Every point is traceable to a Component.
    """
    providers = _providers_with_id(result, matched)
    flag = confidence_flag(result, matched)

    # Rows that assert no single positive identity get a deterministic value.
    if flag == FLAG_ERROR:
        return Confidence(
            flag, CONF_ERROR,
            [Component("Pipeline error", 0.0, result.no_match_reason or "error")],
        )
    if flag == FLAG_AMBIGUOUS:
        return Confidence(
            flag, CONF_AMBIGUOUS,
            [Component("Ambiguous candidates", CONF_AMBIGUOUS, "several entities matched; none chosen")],
        )
    if flag == FLAG_NOT_FOUND:
        return Confidence(
            flag, CONF_NOT_FOUND,
            [Component("No registry entry found", 0.0, "no official number in any source")],
        )

    components: list[Component] = []
    total = 0.0

    # Base — an official registry number was found.
    components.append(Component("Official registry number", W_BASE_ID, f"id {result.registry_id}"))
    total += W_BASE_ID

    # National-register backing (vs. only supplemental sources like GLEIF/Wikidata).
    register_backed = bool(providers & _registry_provider_names())
    components.append(
        Component(
            "National-register backed",
            W_REGISTER_BACKED if register_backed else 0.0,
            "id confirmed by the national company register"
            if register_backed
            else "id only from supplemental sources",
        )
    )
    total += W_REGISTER_BACKED if register_backed else 0.0

    # Independent corroboration — distinct sources carrying the same number.
    n_sources = len(providers)
    corroborated = n_sources >= 2
    components.append(
        Component(
            "Independent corroboration",
            W_CORROBORATION if corroborated else 0.0,
            f"{n_sources} sources carry the same id" if corroborated else "single source",
        )
    )
    total += W_CORROBORATION if corroborated else 0.0

    # Jurisdiction alignment with what the caller asked for.
    asked = (query.jurisdiction or "").strip()
    if not asked:
        components.append(Component("Jurisdiction alignment", W_JURISDICTION_NONE, "no jurisdiction requested"))
        total += W_JURISDICTION_NONE
    else:
        aligned = bool(result.jurisdiction_confirmed) and _country(result.jurisdiction_confirmed) == _country(asked)
        components.append(
            Component(
                "Jurisdiction alignment",
                W_JURISDICTION if aligned else 0.0,
                f"matches requested {asked}" if aligned else f"differs from requested {asked}",
            )
        )
        total += W_JURISDICTION if aligned else 0.0

    # Tier A datapoint coverage (scaled).
    filled = sum(1 for f in _COVERAGE_FIELDS if getattr(result, f, None))
    cov_points = round(W_COVERAGE * filled / len(_COVERAGE_FIELDS), 4)
    components.append(
        Component("Tier A coverage", cov_points, f"{filled}/{len(_COVERAGE_FIELDS)} core fields filled")
    )
    total += cov_points

    # Cross-source contradictions: each unresolved disagreement is a reason for
    # doubt, and any contradiction means the sources didn't actually agree -> a
    # 'verified' row drops to 'probable'.
    n_contra = len(contradictions)
    if n_contra:
        penalty = min(CONTRADICTION_PENALTY_CAP, CONTRADICTION_PENALTY * n_contra)
        components.append(
            Component("Cross-source contradictions", -penalty, f"{n_contra} field(s) disagree across sources")
        )
        total -= penalty
        if flag == FLAG_VERIFIED:
            flag = FLAG_PROBABLE

    value = round(max(0.0, min(total, _CAPS[flag])), 2)
    return Confidence(flag, value, components)
