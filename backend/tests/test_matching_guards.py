"""Precision-over-recall guards in the matching layer: the verifier must abstain
(blank / no_match) rather than emit a match it isn't confident about."""

from app.config import settings
from app.matching.semantic_filter import (
    DECISION_MATCH,
    DECISION_NO_MATCH,
    _assemble_result,
)

_CANDS = [{"name_normalized_register_name": "Example GmbH", "confidence": 0.9}]


def test_low_confidence_match_is_downgraded_to_no_match():
    """A 'match' the verifier rates below the floor must NOT be emitted."""
    floor = settings.min_match_confidence
    low = _assemble_result(
        {"decision": "match", "winning_candidate_index": 0,
         "confidence": floor - 0.1, "reasoning": "unsure"},
        _CANDS,
    )
    assert low["decision"] == DECISION_NO_MATCH
    assert low["winning_candidate"] is None


def test_confident_match_is_kept():
    high = _assemble_result(
        {"decision": "match", "winning_candidate_index": 0,
         "confidence": 0.95, "reasoning": "clear"},
        _CANDS,
    )
    assert high["decision"] == DECISION_MATCH
    assert high["winning_candidate"]["name_normalized_register_name"] == "Example GmbH"
