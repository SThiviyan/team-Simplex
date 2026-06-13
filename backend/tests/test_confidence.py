"""Deterministic, additive confidence scoring (app/pipeline/confidence.py).

Every assertion pins the number to a sum of named evidence signals, so the
score is reproducible and traceable rather than a capped subjective value.
"""

from app.pipeline.confidence import (
    CONF_AMBIGUOUS,
    FLAG_AMBIGUOUS,
    FLAG_ERROR,
    FLAG_NOT_FOUND,
    FLAG_PROBABLE,
    FLAG_VERIFIED,
    compute_confidence,
)
from app.pipeline.models import ExtractionResult, QueryRow


def _row(**kw) -> ExtractionResult:
    return ExtractionResult(query_id="q", **kw)


def _rec(**kw) -> dict:
    base = {"registry_id": None, "name_normalized_register_name": None, "provider": None}
    return {**base, **kw}


_Q_DE = QueryRow(query_id="q", name="X", jurisdiction="DE")


def test_register_backed_full_coverage_is_verified_near_one():
    row = _row(
        registry_id="HRB 1", jurisdiction_confirmed="DE",
        registered_address="Street 1", incorporation_date="2000-01-01",
        organization_type="GmbH", status="active",
    )
    matched = [_rec(registry_id="HRB 1", provider="handelsregister")]
    c = compute_confidence(row, matched, [], _Q_DE)
    # 0.50 base + 0.20 register-backed + 0.10 jurisdiction + 0.08 full Tier A
    # (no second source -> no corroboration) = 0.88.
    assert c.flag == FLAG_VERIFIED
    assert c.value == 0.88
    assert {comp.label for comp in c.components} >= {
        "Official registry number", "National-register backed", "Tier A coverage"
    }


def test_corroboration_adds_points_and_verifies():
    row = _row(registry_id="123", jurisdiction_confirmed="DE")
    matched = [_rec(registry_id="123", provider="gleif"), _rec(registry_id="123", provider="wikidata")]
    c = compute_confidence(row, matched, [], _Q_DE)
    # 0.50 base + 0.12 corroboration + 0.10 jurisdiction (no register backing,
    # no Tier A) = 0.72; two independent sources agreeing -> verified.
    assert c.flag == FLAG_VERIFIED
    assert c.value == 0.72


def test_single_non_register_source_is_probable():
    row = _row(registry_id="123", jurisdiction_confirmed="DE")
    matched = [_rec(registry_id="123", provider="gleif")]
    c = compute_confidence(row, matched, [], _Q_DE)
    # 0.50 base + 0.10 jurisdiction = 0.60.
    assert c.flag == FLAG_PROBABLE
    assert c.value == 0.6


def test_no_jurisdiction_requested_gets_half_credit():
    row = _row(registry_id="123", jurisdiction_confirmed="DE")
    matched = [_rec(registry_id="123", provider="gleif")]
    c = compute_confidence(row, matched, [], QueryRow(query_id="q", name="X", jurisdiction=""))
    assert c.value == 0.55  # 0.50 + 0.05


def test_contradiction_penalizes_and_downgrades_verified():
    row = _row(
        registry_id="HRB 1", jurisdiction_confirmed="DE",
        registered_address="Street 1", incorporation_date="2000-01-01",
        organization_type="GmbH", status="active",
    )
    matched = [_rec(registry_id="HRB 1", provider="handelsregister")]
    contradictions = [{"field": "incorporation_date", "values": ["2000", "2001"]}]
    c = compute_confidence(row, matched, contradictions, _Q_DE)
    # Would-be verified 0.88, minus 0.10 contradiction = 0.78, and any
    # contradiction forces probable (-> capped at 0.85, no effect here).
    assert c.flag == FLAG_PROBABLE
    assert c.value == 0.78


def test_abstention_rows_are_deterministic():
    assert compute_confidence(_row(no_match_reason="layer1_error: x"), [], [], _Q_DE).value == 0.0
    assert compute_confidence(_row(no_match_reason="layer1_error: x"), [], [], _Q_DE).flag == FLAG_ERROR
    nf = compute_confidence(_row(no_match_reason="not_in_registry"), [], [], _Q_DE)
    assert nf.flag == FLAG_NOT_FOUND and nf.value == 0.0
    amb = compute_confidence(_row(no_match_reason="ambiguous_candidates"), [], [], _Q_DE)
    assert amb.flag == FLAG_AMBIGUOUS and amb.value == CONF_AMBIGUOUS
