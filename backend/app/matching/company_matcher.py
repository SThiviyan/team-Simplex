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

from app.matching.token_weights import (
    token_weights,
    weighted_containment_score,
    weighted_token_score,
)

# Field names in the source records. Kept here so a different schema only
# needs editing in one place.
NAME_FIELD = "name_normalized_register_name"
JURISDICTION_FIELD = "jurisdiction_confirmed"
CONFIDENCE_FIELD = "confidence"

# Default scorer: WRatio (RapidFuzz's adaptive scorer) handles subset/extra
# tokens and partial overlaps better than a single ratio — the FuzzyAI choice.
# The legal-form-stripped comparison and the Fellegi-Sunter weighted token score
# (token_weights.py) complement it in score_record.
DEFAULT_SCORER: Callable[..., float] = fuzz.WRatio

# ---------------------------------------------------------------------------
# Legal-form suffix stripping (multi-country).
#
# "Sinpex" should match "Sinpex GmbH" at ~1.0, not ~0.7: the legal form is not
# evidence about identity. The token sequences below are matched (lowercased,
# punctuation-free — i.e. AFTER rapidfuzz's default_process, so "e.V." is
# "e v", "S.p.A." is "s p a", "Sp. z o.o." is "sp z o o") and stripped from the
# END of a name, repeatedly, never consuming the whole name. The fuzzy score is
# then the max of the raw and the suffix-stripped comparison, so stripping can
# only ever help.
# ---------------------------------------------------------------------------
_LEGAL_FORM_SEQUENCES: list[tuple[str, ...]] = [
    tuple(form.split())
    for form in (
        # Germany / Austria / Switzerland
        "gmbh co kg", "gmbh co kgaa", "gmbh u co kg", "ag co kg", "se co kgaa",
        "ug haftungsbeschrankt", "ggmbh", "gmbh", "mbh", "kgaa", "ohg", "gbr",
        "ag", "kg", "ug", "se", "e v", "ev", "e g", "e k", "ek",
        # UK / US / IE / global English
        "incorporated", "corporation", "company", "limited", "ltd", "plc",
        "llp", "llc", "pllc", "inc", "corp", "lp", "pc", "co", "pty", "dac",
        "clg", "ulc", "teo",
        # France / Belgium / Luxembourg
        "sasu", "sarl", "eurl", "sas", "snc", "sci", "scop", "sa",
        # Spain / Portugal / Latin America
        "sa de cv", "s de rl de cv", "sapi de cv", "slu", "sl", "ltda",
        "eireli", "cia",
        # Italy
        "srls", "spa", "srl", "s p a", "s r l",
        # Netherlands / Belgium
        "bvba", "cvba", "vof", "nv", "bv", "cv",
        # Nordics
        "oyj", "oy ab", "oy", "ab", "asa", "aps", "a s", "k s", "ans", "abp",
        "hb", "kb", "as",
        # Poland / Czechia / Slovakia
        "sp z o o", "sp z oo", "spzoo", "sp j", "sp k", "s r o", "sro",
        "v o s",
        # Hungary
        "kft", "zrt", "nyrt", "bt", "kkt",
        # Eastern Europe / CIS
        "ooo", "oao", "zao", "pao", "tov", "d o o", "doo",
        # Joint-stock / misc international
        "pjsc", "cjsc", "jsc", "psc",
        # Japan (romanised) / Asia-Pacific
        "kabushiki kaisha", "godo kaisha", "yugen kaisha", "kk", "gk", "yk",
        "pte ltd", "pte", "pvt", "sdn bhd", "sdn", "bhd",
        # Trailing connectives left behind by the forms above ("X & Co.")
        "und co", "and co", "et cie", "cie",
    )
]
# Longest sequences first so "gmbh co kg" wins over "kg".
_LEGAL_FORM_SEQUENCES.sort(key=len, reverse=True)


def strip_legal_suffix(processed: str) -> str:
    """Remove trailing legal-form token sequences from a default_process'd name.

    Strips repeatedly ("... GmbH & Co. KG" loses "co kg" then "gmbh") but never
    consumes the entire name, so a company literally named "AG" survives.
    """
    tokens = processed.split()
    changed = True
    while changed and tokens:
        changed = False
        for form in _LEGAL_FORM_SEQUENCES:
            n = len(form)
            if len(tokens) > n and tuple(tokens[-n:]) == form:
                tokens = tokens[:-n]
                changed = True
                break
    return " ".join(tokens)


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
    name_score: float,
    jurisdiction_match: bool,
    *,
    jurisdiction_penalty: float = 0.5,
) -> float:
    """Confidence from the NAME match alone.

    The gather layer attaches its own ``confidence`` (a crude difflib name
    ratio), but the matching stages deliberately IGNORE it — we filter and rank
    on the name match only. A jurisdiction mismatch then multiplies the result by
    ``jurisdiction_penalty`` (demote, don't drop) rather than counting against
    the name. The output is clamped to ``[0.0, 1.0]``.
    """
    conf = name_score
    if not jurisdiction_match:
        conf *= jurisdiction_penalty
    return round(min(max(conf, 0.0), 1.0), 4)


def score_record(
    record: dict[str, Any],
    target: Target,
    *,
    scorer: Callable[..., float] = DEFAULT_SCORER,
    jurisdiction_penalty: float = 0.5,
    tf_weights: dict[str, float] | None = None,
) -> Candidate:
    """Score a single record against the target and build a Candidate.

    ``tf_weights`` are the Fellegi-Sunter inverse-frequency token weights over
    the candidate set (from :func:`find_candidates`); when given, the weighted
    token score is folded into the name score so a discriminative core name
    outweighs legal-form / generic filler.
    """
    raw_name = record.get(NAME_FIELD)
    # RapidFuzz scorers return 0..100; normalize to 0..1. A missing/empty
    # name scores 0 (it can never match), which the gross filter will drop.
    if raw_name:
        raw_score = scorer(
            target.name, raw_name, processor=utils.default_process
        ) / 100.0
        # Legal-form tolerance: also compare the suffix-stripped core names
        # ("sinpex" vs "sinpex" for "Sinpex" / "Sinpex GmbH") and keep the
        # better of the two scores — the legal form must never count against
        # a match, in any jurisdiction (GmbH, Inc, e.V., S.p.A., Kft, …).
        core_target = strip_legal_suffix(utils.default_process(target.name))
        core_name = strip_legal_suffix(utils.default_process(raw_name))
        core_score = (
            scorer(core_target, core_name) / 100.0 if core_target and core_name else 0.0
        )
        weights = tf_weights if tf_weights is not None else {}
        # Fellegi-Sunter weighted token score (Splink technique): rewards
        # agreement on rare, identifying tokens; near-ignores generic ones.
        tf_score = weighted_token_score(target.name, raw_name, weights)
        # Containment: reward the query being *contained* in the candidate
        # without penalising extra text ("Nestle" -> "Nestle S.A."). This is the
        # recall-oriented signal the user asked for; precision is handled by the
        # fame boost and the LLM re-rank downstream.
        contain_score = weighted_containment_score(target.name, raw_name, weights)
        name_score = max(raw_score, core_score, tf_score, contain_score)
    else:
        name_score = 0.0

    jurisdiction_match = _normalize_jurisdiction(
        record.get(JURISDICTION_FIELD)
    ) == _normalize_jurisdiction(target.jurisdiction)

    # NOTE: the source's own gather-time confidence (a crude difflib name ratio)
    # is deliberately NOT used or surfaced — it only sorted out otherwise-correct
    # results. The matching stages filter and rank on the name match alone.
    confidence = update_confidence(
        name_score,
        jurisdiction_match,
        jurisdiction_penalty=jurisdiction_penalty,
    )

    return Candidate(
        record=record,
        name_score=round(name_score, 4),
        jurisdiction_match=jurisdiction_match,
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
    jurisdiction_penalty:
        Passed through to :func:`update_confidence` (jurisdiction-mismatch demote).

    Returns
    -------
    list[Candidate]
        Sorted by name-based confidence (desc), then raw name score (desc).
    """
    companies = list(companies)
    # Fellegi-Sunter token weights computed over the whole candidate set + the
    # query, so token rarity is judged in context.
    tf_weights = token_weights([c.get(NAME_FIELD) or "" for c in companies] + [target.name])
    scored = [
        score_record(
            record,
            target,
            scorer=scorer,
            jurisdiction_penalty=jurisdiction_penalty,
            tf_weights=tf_weights,
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
    }
    return out
