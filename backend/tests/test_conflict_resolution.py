"""Tests for cross-referencing conflicting fields among same-entity records."""

from app.matching.conflict_resolution import cross_reference, resolve_field
from app.matching.corroboration import corroborate


def _rec(provider, address, *, last_update=None, registry_id="HRB 1", name="ACME GmbH"):
    return {
        "name_normalized_register_name": name,
        "jurisdiction_confirmed": "DE",
        "registry_id": registry_id,
        "registry_court": "Amtsgericht München",
        "provider": provider,
        "address": address,
        "last_update": last_update,
        "confidence": 0.9,
        "source": f"https://{provider}.example/acme",
    }


def test_no_conflict_returns_none():
    recs = [_rec("handelsregister", "Hauptstr. 1, 80331 München"),
            _rec("wikidata", "Hauptstr. 1, 80331 München")]
    assert resolve_field("address", recs) is None


def test_official_register_beats_aggregator():
    # Same company, two different addresses: the official national register must
    # win over the crowd-sourced aggregator.
    official = _rec("handelsregister", "Hauptstr. 1, 80331 München")
    crowd = _rec("wikidata", "Altestr. 99, 10115 Berlin")
    res = resolve_field("address", [official, crowd])
    assert res is not None
    assert res["chosen"] == "Hauptstr. 1, 80331 München"
    assert "higher-authority" in res["reason"]


def test_component_corroboration_breaks_tie_between_equal_authority():
    # Two equally authoritative registers disagree; a third record corroborates
    # one address's postal code / city, tipping the balance.
    a = _rec("companies_house", "Hauptstr. 1, 80331 München", registry_id="R-A")
    b = _rec("brreg", "Altestr. 99, 10115 Berlin", registry_id="R-B")
    corrob = _rec("annuaire", "Nebenweg 5, 80331 München", registry_id="R-C")
    res = resolve_field("address", [a, b, corrob])
    assert res["chosen"] == "Hauptstr. 1, 80331 München"  # 80331/München recur
    assert res["alternatives"][0]["component_support"] >= 2


def test_recency_is_final_tiebreaker():
    # Equal authority, no component edge, equal votes → newest wins.
    old = _rec("companies_house", "Old Road 1, 99999 Town", last_update="2019-01-01")
    new = _rec("brreg", "New Road 2, 88888 City", last_update="2024-06-01")
    res = resolve_field("address", [old, new])
    assert res["chosen"] == "New Road 2, 88888 City"


def test_verifier_breaks_a_true_tie():
    # Identical authority/votes/recency and non-address field (no component
    # signal): an external verifier decides.
    a = {"provider": "companies_house", "status": "active"}
    b = {"provider": "brreg", "status": "in_liquidation"}
    picked = resolve_field("status", [a, b], verifier=lambda f, vals: "in_liquidation")
    assert picked["chosen"] == "in_liquidation"


def test_cross_reference_writes_winner_and_trail_into_merged():
    official = _rec("handelsregister", "Hauptstr. 1, 80331 München")
    crowd = _rec("wikidata", "Altestr. 99, 10115 Berlin")
    merged = dict(crowd)  # pretend the naive merge picked the wrong one
    cross_reference([official, crowd], merged)
    assert merged["address"] == "Hauptstr. 1, 80331 München"
    assert merged["_conflicts"]
    assert merged["_conflicts"][0]["field"] == "address"


def test_corroborate_resolves_conflict_end_to_end():
    # Same registry_id → one merged entity; the better-supported address is kept.
    official = _rec("handelsregister", "Hauptstr. 1, 80331 München")
    crowd = _rec("wikidata", "Altestr. 99, 10115 Berlin")
    merged = corroborate([crowd, official])
    assert len(merged) == 1
    assert merged[0]["address"] == "Hauptstr. 1, 80331 München"
    assert merged[0]["_provider_count"] == 2
    assert merged[0]["_conflicts"][0]["chosen"] == "Hauptstr. 1, 80331 München"


def test_single_record_has_no_conflicts_key():
    merged = corroborate([_rec("handelsregister", "Hauptstr. 1, 80331 München")])
    assert "_conflicts" not in merged[0]
