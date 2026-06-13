"""Tests for abbreviation expansion and the recursive-search identical guard."""

from app.matching.name_expansion import expand_abbreviations, normalized_equal
from app.matching.semantic_filter import (
    DECISION_NO_MATCH,
    DECISION_RECURSIVE,
    _assemble_result,
)


# --- expander -----------------------------------------------------------------

def test_expands_legal_form_and_name_words():
    out = expand_abbreviations("Müller Intl GmbH", "DE")
    assert out == "Müller International Gesellschaft mit beschränkter Haftung"


def test_expands_ltd_and_ampersand():
    assert expand_abbreviations("Smith & Co Ltd", "GB") == "Smith and Company Limited"


def test_whole_token_only_does_not_touch_substrings():
    # "Coca" must not be mangled by the "Co" rule.
    assert expand_abbreviations("Coca Cola Co", "US") == "Coca Cola Company"


def test_jurisdiction_disambiguates_ambiguous_suffix():
    assert expand_abbreviations("Acme SA", "ES").endswith("Sociedad Anónima")
    assert expand_abbreviations("Acme SA", "FR").endswith("Société Anonyme")


def test_no_abbreviations_returns_unchanged():
    assert expand_abbreviations("Siemens Healthineers", "DE") == "Siemens Healthineers"


def test_normalized_equal_ignores_case_and_punctuation():
    assert normalized_equal("ACME, GmbH", "acme  gmbh")
    assert not normalized_equal("ACME GmbH", "ACME AG")


# --- recursive-search guard ---------------------------------------------------

def _recursive_input(suggested: str) -> dict:
    return {
        "decision": DECISION_RECURSIVE,
        "winning_candidate_index": -1,
        "confidence": 0.5,
        "reasoning": "no candidate matched",
        "suggested_query": suggested,
    }


def test_identical_suggestion_is_expanded_not_echoed():
    # Model echoed the query verbatim -> guard expands its abbreviations.
    res = _assemble_result(
        _recursive_input("ACME GmbH"), [], query_name="ACME GmbH", jurisdiction="DE"
    )
    assert res["decision"] == DECISION_RECURSIVE
    suggested = res["recursive_search"]["suggested_query"]
    assert suggested == "ACME Gesellschaft mit beschränkter Haftung"
    assert not normalized_equal(suggested, "ACME GmbH")


def test_identical_suggestion_with_nothing_to_expand_downgrades_to_no_match():
    res = _assemble_result(
        _recursive_input("Siemens Healthineers"),
        [],
        query_name="Siemens Healthineers",
        jurisdiction="DE",
    )
    assert res["decision"] == DECISION_NO_MATCH
    assert res["recursive_search"] is None


def test_different_suggestion_passes_through_untouched():
    res = _assemble_result(
        _recursive_input("Bayerische Motoren Werke Aktiengesellschaft"),
        [],
        query_name="BMW",
        jurisdiction="DE",
    )
    assert res["decision"] == DECISION_RECURSIVE
    assert (
        res["recursive_search"]["suggested_query"]
        == "Bayerische Motoren Werke Aktiengesellschaft"
    )
