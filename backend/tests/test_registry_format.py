"""Unit tests for country-standard registry_id / registry_court normalisation."""

from app.search.base import SearchResult
from app.search.registry_format import normalize_date, normalize_registry, normalize_status


def test_de_handelsregister_id_and_court():
    # Already-conventional GLEIF values (localOrganizationName) are preserved.
    assert normalize_registry("DE", "hrb 42243", "Amtsgericht München") == (
        "HRB 42243",
        "Amtsgericht München",
    )
    # An English "Local Court <city>" is mapped to the German "Amtsgericht <city>"
    # form (the city name itself is left as the source gives it).
    assert normalize_registry("DE", None, "Local Court Munich")[1] == "Amtsgericht Munich"
    # Branch suffix and zero-padding are normalised.
    assert normalize_registry("DE", "HRB0001234B", None)[0] == "HRB 1234 B"


def test_at_firmenbuchnummer_gets_fn_prefix():
    assert normalize_registry("AT", "56247t", None)[0] == "FN 56247 t"
    assert normalize_registry("AT", "FN 56247 T", None)[0] == "FN 56247 t"


def test_fr_siren_grouped():
    assert normalize_registry("FR", "442962239", None)[0] == "442 962 239"
    # Already grouped stays grouped.
    assert normalize_registry("FR", "542 014 428", None)[0] == "542 014 428"


def test_no_orgnummer_grouped():
    assert normalize_registry("NO", "923609016", None)[0] == "923 609 016"


def test_unknown_values_pass_through():
    # A non-registry identifier on a known jurisdiction is left intact.
    assert normalize_registry("DE", "LEI123", None)[0] == "LEI123"
    # Jurisdictions without a normaliser are only whitespace-trimmed.
    assert normalize_registry("US", "  0000320193 ", None)[0] == "0000320193"
    assert normalize_registry(None, None, None) == (None, None)


def test_status_maps_to_common_vocabulary():
    assert normalize_status("ACTIVE") == "active"
    assert normalize_status("aktiv") == "active"
    assert normalize_status("Registered") == "active"
    assert normalize_status("slettet") == "dissolved"
    assert normalize_status("In Liquidation") == "in_liquidation"
    assert normalize_status("ceased") == "dissolved"
    # Unknown values become snake_case rather than being dropped.
    assert normalize_status("Voluntary strike off") == "voluntary_strike_off"
    assert normalize_status("Some Novel State") == "some_novel_state"
    assert normalize_status(None) is None


def test_incorporation_date_reduced_to_iso_calendar_date():
    assert normalize_date("1916-02-19T23:00:00Z") == "1916-02-19"
    assert normalize_date("2001-09-11") == "2001-09-11"
    assert normalize_date(None) is None


def test_searchresult_applies_normalisation_on_construction():
    r = SearchResult(
        title="Bayerische Motoren Werke AG",
        snippet="",
        score=1.0,
        source="gleif",
        jurisdiction="DE",
        registry_id="hrb 42243",
        registry_court="Amtsgericht München",
        status="ACTIVE",
        incorporation_date="1916-02-19T23:00:00Z",
    )
    assert r.registry_id == "HRB 42243"
    assert r.registry_court == "Amtsgericht München"
    assert r.status == "active"
    assert r.incorporation_date == "1916-02-19"
