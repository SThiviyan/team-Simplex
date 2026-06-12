"""Gross filtering and ranking of company-registry candidates with RapidFuzz.

Given a list of registry records and a target defined as
``(name, jurisdiction)``, this module performs a cheap fuzzy pre-filter
("gross filtering") to drop obviously irrelevant rows, ranks the survivors,
returns the top candidates (count is selectable), and re-derives each
candidate's confidence from the fuzzy evidence.

The input records use the schema emitted by the gather layer
(``app.search.csv_search``), e.g.:

    {
        "query_id": "q1",
        "registry_id": "HRB 210455 B",
        "registry_court": "Amtsgericht Charlottenburg",
        "name_normalized_register_name": "Sinpex GmbH",
        "jurisdiction_confirmed": "DE",
        "confidence": 0.98,
        "source": "https://www.handelsregister.de/...",
        "no_match_reason": null,
        "last_update": "2024-01-05",
        "address": "Musterstr. 1, 10115 Berlin, DE",
        "organization_type": "GmbH"
    }

Only ``name_normalized_register_name``, ``jurisdiction_confirmed`` and
``confidence`` are read; everything else (address, last_update,
organization_type, …) is carried through untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from rapidfuzz import fuzz, utils

# Field names in the source records. Kept here so a different schema only
# needs editing in one place.
NAME_FIELD = "name_normalized_register_name"
JURISDICTION_FIELD = "jurisdiction_confirmed"
CONFIDENCE_FIELD = "confidence"

# Default scorer: token_sort_ratio is robust to word order ("Sinpex GmbH"
# vs "GmbH Sinpex") and to extra tokens, which is what registry names need.
DEFAULT_SCORER: Callable[..., float] = fuzz.token_sort_ratio


@dataclass(frozen=True)
class Target:
    """The thing we are searching for."""

    name: str
    jurisdiction: str


@dataclass
class Candidate:
    """A scored registry record.

    ``record`` is the original, unmodified JSON object. The derived fields
    describe how well it matched the target.
    """

    record: dict[str, Any]
    name_score: float            # fuzzy name similarity, 0..1
    jurisdiction_match: bool     # did the jurisdiction agree?
    prior_confidence: float      # the record's original confidence, 0..1
    confidence: float            # confidence after folding in the match, 0..1
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str | None:
        return self.record.get(NAME_FIELD)

    @property
    def jurisdiction(self) -> str | None:
        return self.record.get(JURISDICTION_FIELD)


def _normalize_jurisdiction(value: str | None) -> str:
    return (value or "").strip().upper()


def update_confidence(
    prior_confidence: float,
    name_score: float,
    jurisdiction_match: bool,
    *,
    prior_weight: float = 0.4,
    jurisdiction_penalty: float = 0.5,
) -> float:
    """Re-derive a confidence value from the prior confidence and the match.

    The result blends two independent pieces of evidence:

    * ``prior_confidence`` — what the source already believed (0..1).
    * ``name_score``       — how well the name matched the target (0..1).

    They are combined as a weighted average controlled by ``prior_weight``
    (the share given to the prior; the name score gets the rest). A
    jurisdiction mismatch then multiplies the result by
    ``jurisdiction_penalty`` rather than discarding the row outright, so a
    strong name match in the "wrong" jurisdiction is demoted but still
    surfaceable.

    The output is clamped to ``[0.0, 1.0]``.
    """
    prior_weight = min(max(prior_weight, 0.0), 1.0)
    blended = prior_weight * prior_confidence + (1.0 - prior_weight) * name_score
    if not jurisdiction_match:
        blended *= jurisdiction_penalty
    return round(min(max(blended, 0.0), 1.0), 4)


def score_record(
    record: dict[str, Any],
    target: Target,
    *,
    scorer: Callable[..., float] = DEFAULT_SCORER,
    prior_weight: float = 0.4,
    jurisdiction_penalty: float = 0.5,
) -> Candidate:
    """Score a single record against the target and build a Candidate."""
    raw_name = record.get(NAME_FIELD)
    # RapidFuzz scorers return 0..100; normalize to 0..1. A missing/empty
    # name scores 0 (it can never match), which the gross filter will drop.
    if raw_name:
        name_score = scorer(
            target.name, raw_name, processor=utils.default_process
        ) / 100.0
    else:
        name_score = 0.0

    jurisdiction_match = _normalize_jurisdiction(
        record.get(JURISDICTION_FIELD)
    ) == _normalize_jurisdiction(target.jurisdiction)

    prior = record.get(CONFIDENCE_FIELD)
    prior = float(prior) if isinstance(prior, (int, float)) else 0.0

    confidence = update_confidence(
        prior,
        name_score,
        jurisdiction_match,
        prior_weight=prior_weight,
        jurisdiction_penalty=jurisdiction_penalty,
    )

    return Candidate(
        record=record,
        name_score=round(name_score, 4),
        jurisdiction_match=jurisdiction_match,
        prior_confidence=round(prior, 4),
        confidence=confidence,
    )


def find_candidates(
    companies: Iterable[dict[str, Any]],
    target: Target,
    *,
    top_n: int = 5,
    score_cutoff: float = 0.6,
    require_jurisdiction: bool = False,
    scorer: Callable[..., float] = DEFAULT_SCORER,
    prior_weight: float = 0.4,
    jurisdiction_penalty: float = 0.5,
) -> list[Candidate]:
    """Gross-filter, rank, and return the top candidates for ``target``.

    Parameters
    ----------
    companies:
        Iterable of registry records.
    target:
        The ``(name, jurisdiction)`` we are looking for.
    top_n:
        How many candidates to return. The selectable "parameter" — set to
        ``0`` (or a negative number) to return every row that survives the
        filter.
    score_cutoff:
        Gross-filter threshold on the fuzzy *name* score (0..1). Rows below
        this are discarded before ranking. This is the cheap relevance gate.
    require_jurisdiction:
        If True, also drop rows whose jurisdiction does not match the target
        during gross filtering. If False, mismatches survive but are demoted
        via ``jurisdiction_penalty``.
    scorer:
        Any RapidFuzz scorer (``fuzz.ratio``, ``fuzz.WRatio``,
        ``fuzz.token_sort_ratio`` …). Returns 0..100.
    prior_weight / jurisdiction_penalty:
        Passed through to :func:`update_confidence`.

    Returns
    -------
    list[Candidate]
        Sorted by updated confidence (desc), then raw name score (desc).
    """
    scored = [
        score_record(
            record,
            target,
            scorer=scorer,
            prior_weight=prior_weight,
            jurisdiction_penalty=jurisdiction_penalty,
        )
        for record in companies
    ]

    # Gross filtering: drop anything below the name-score cutoff (and, if
    # requested, anything in the wrong jurisdiction).
    survivors = [c for c in scored if c.name_score >= score_cutoff]
    if require_jurisdiction:
        survivors = [c for c in survivors if c.jurisdiction_match]

    survivors.sort(key=lambda c: (c.confidence, c.name_score), reverse=True)

    if top_n and top_n > 0:
        return survivors[:top_n]
    return survivors


def candidate_to_dict(candidate: Candidate) -> dict[str, Any]:
    """Flatten a Candidate to a JSON-serializable dict.

    Keeps the original record and overwrites its ``confidence`` with the
    updated value, while exposing the match diagnostics alongside.
    """
    out = dict(candidate.record)
    out[CONFIDENCE_FIELD] = candidate.confidence
    out["_match"] = {
        "name_score": candidate.name_score,
        "jurisdiction_match": candidate.jurisdiction_match,
        "prior_confidence": candidate.prior_confidence,
    }
    return out
