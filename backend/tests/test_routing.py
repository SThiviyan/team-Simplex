"""Tests for jurisdiction/cost-aware source routing."""

from app.search.routing import describe, has_identifier, rank_providers
from app.search.sources import all_providers

PS = all_providers()


def _names(providers):
    return [p.name for p in providers]


def test_has_identifier():
    assert has_identifier("KRS 0000028860")
    assert has_identifier("33.000.167/0001-01")
    assert not has_identifier("Volkswagen")
    assert not has_identifier(None)


def test_free_excludes_premium():
    de = rank_providers(PS, "DE", include_premium=False)
    assert all(p.cost == "free" for p in de)
    assert "northdata" not in _names(de)  # premium
    assert "handelsregister" in _names(de)


def test_premium_included_after_free():
    de = rank_providers(PS, "DE", include_premium=True)
    costs = [p.cost for p in de]
    # all free entries come before any premium entry
    assert costs == sorted(costs, key=lambda c: 0 if c == "free" else 1)
    assert "northdata" in _names(de)


def test_register_ranked_before_global():
    de = _names(rank_providers(PS, "DE", include_premium=False))
    assert de.index("handelsregister") < de.index("gleif")


def test_number_only_excluded_without_identifier():
    pl = _names(rank_providers(PS, "PL", include_premium=True, identifier_present=False))
    assert "krs_pl" not in pl  # lookup == number
    assert "apify_krs_pl" not in pl
    pl_id = _names(rank_providers(PS, "PL", include_premium=True, identifier_present=True))
    assert "krs_pl" in pl_id


def test_jurisdiction_relevance():
    us = _names(rank_providers(PS, "US", include_premium=True))
    assert "handelsregister" not in us  # DE-only
    assert "sec" in us
    assert "opencorporates" in us  # global
    assert "gleif" in us


def test_describe_metadata():
    gleif = describe(next(p for p in PS if p.name == "gleif"))
    assert gleif["tier"] == "global" and gleif["cost"] == "free"
    assert gleif["jurisdictions"] is None
    nd = describe(next(p for p in PS if p.name == "northdata"))
    assert nd["cost"] == "premium"
