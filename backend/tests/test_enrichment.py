"""Layer-2 enrichment: deterministic merge, calibration flags, normalization."""

from app.pipeline.enrichment import (
    FLAG_AMBIGUOUS,
    FLAG_ERROR,
    FLAG_NOT_FOUND,
    FLAG_PROBABLE,
    FLAG_VERIFIED,
    _confidence_flag,
    _echo_jurisdiction,
    _merge_from_records,
    matching_records,
    normalize_date,
    normalize_status,
)
from app.pipeline.models import ExtractionResult, QueryRow


def _row(**kw) -> ExtractionResult:
    return ExtractionResult(query_id="q", **kw)


def _record(**kw) -> dict:
    base = {
        "query_id": "q", "registry_id": None, "registry_court": None,
        "name_normalized_register_name": None, "jurisdiction_confirmed": None,
        "confidence": 0.9, "source": None, "no_match_reason": None,
        "provider": None, "snippet": None, "address": None,
        "organization_type": None, "status": None, "last_update": None,
        "incorporation_date": None, "metadata": {},
    }
    return {**base, **kw}


def test_normalize_status_maps_register_vocabulary():
    assert normalize_status("ACTIVE") == "active"
    assert normalize_status("aktuell") == "active"
    assert normalize_status("gelöscht") == "dissolved"
    assert normalize_status("ophørt") == "dissolved"
    assert normalize_status("in Liquidation") == "in_liquidation"
    assert normalize_status("Dormant") == "dormant"
    assert normalize_status("  ") is None
    assert normalize_status("something else") == "something else"  # pass-through, no guess


def test_normalize_date_iso_and_local_formats():
    assert normalize_date("2019-02-20") == "2019-02-20"
    assert normalize_date("2019-02-20T00:00:00Z") == "2019-02-20"
    assert normalize_date("20.02.2019") == "2019-02-20"
    assert normalize_date("20/02/2019") == "2019-02-20"
    assert normalize_date("1947") == "1947"
    assert normalize_date(None) is None


def test_jurisdiction_echo_uk_gb_interchangeable():
    q_uk = QueryRow(query_id="q", name="Tesco", jurisdiction="UK")
    q_gb = QueryRow(query_id="q", name="Tesco", jurisdiction="GB")
    assert _echo_jurisdiction(_row(jurisdiction_confirmed="GB"), q_uk) == "UK"
    assert _echo_jurisdiction(_row(jurisdiction_confirmed="UK"), q_gb) == "GB"
    assert _echo_jurisdiction(_row(jurisdiction_confirmed="GB"), q_gb) == "GB"
    # State codes: same place, caller's spelling wins.
    q_usca = QueryRow(query_id="q", name="Apple", jurisdiction="US-CA")
    assert _echo_jurisdiction(_row(jurisdiction_confirmed="US-CA"), q_usca) == "US-CA"
    # Genuinely different jurisdiction is NOT rewritten.
    q_de = QueryRow(query_id="q", name="X", jurisdiction="DE")
    assert _echo_jurisdiction(_row(jurisdiction_confirmed="AT"), q_de) == "AT"
    assert _echo_jurisdiction(_row(jurisdiction_confirmed=None), q_de) is None


def test_confidence_flags():
    # error rows
    assert _confidence_flag(_row(no_match_reason="layer1_error: boom"), []) == FLAG_ERROR
    # ambiguity
    assert _confidence_flag(_row(no_match_reason="ambiguous_candidates"), []) == FLAG_AMBIGUOUS
    # honest blank
    assert _confidence_flag(_row(no_match_reason="not_in_registry"), []) == FLAG_NOT_FOUND
    # registry-provider-backed ID -> verified
    rec = _record(registry_id="HRB 1", provider="handelsregister")
    assert _confidence_flag(_row(registry_id="HRB 1"), [rec]) == FLAG_VERIFIED
    # two independent sources agreeing -> verified
    recs = [_record(registry_id="123", provider="gleif"),
            _record(registry_id="123", provider="wikidata")]
    assert _confidence_flag(_row(registry_id="123"), recs) == FLAG_VERIFIED
    # single non-registry source -> probable
    assert _confidence_flag(_row(registry_id="123"), recs[:1]) == FLAG_PROBABLE


def test_merge_prefers_registry_over_gleif():
    row = _row(registry_id="HRB 1", name_normalized_register_name="Acme GmbH")
    records = [
        _record(registry_id="HRB 1", provider="gleif", address="GLEIF St 1, Berlin, DE",
                organization_type="GMBH"),
        _record(registry_id="HRB 1", provider="handelsregister",
                address="Registry Str 2, 10115, Berlin, DE", status="aktuell"),
        # A DIFFERENT entity's record must not leak in.
        _record(registry_id="HRB 999", provider="handelsregister",
                name_normalized_register_name="Other AG",
                incorporation_date="01.01.1990"),
    ]
    matched = matching_records(row, records)
    assert all(r["registry_id"] != "HRB 999" for r in matched)
    merged = _merge_from_records(row, matched)
    assert merged.registered_address == "Registry Str 2, 10115, Berlin, DE"  # registry beats gleif
    assert merged.status == "active"
    assert merged.organization_type == "GMBH"  # gleif fills what the registry lacked
    assert merged.incorporation_date is None   # other entity's date NOT leaked


def test_matching_records_tolerates_leading_zeros():
    row = _row(registry_id="445790")
    rec = _record(registry_id="00445790", provider="gleif")
    assert matching_records(row, [rec])


def test_impressum_extracts_mandated_facts():
    from app.integrations.impressum import extract_company_facts

    text = """Impressum
    posylka.de GmbH
    Overhagener Weg 36, 59597 Erwitte
    Geschäftsführer: Maxim Acht
    Registergericht: Amtsgericht Paderborn HRB 10033
    USt-IdNr.: DE266929333
    """
    facts = extract_company_facts(text)
    assert facts["registry_id"] == "HRB 10033"
    assert facts["registry_court"] == "Amtsgericht Paderborn"
    assert facts["officers"] == "Geschäftsführer: Maxim Acht"
    assert facts["vat_number"] == "DE266929333"
    assert facts["registered_address"] == "Overhagener Weg 36, 59597 Erwitte"

    # Austrian Firmenbuch pattern.
    at = extract_company_facts("Firmenbuchnummer: FN 56247t, Landesgericht Salzburg")
    assert at["registry_id"] == "FN 56247t"
    assert at["registry_court"] == "Landesgericht Salzburg"

    # A bare contact page (address only) yields no register facts.
    assert "registry_id" not in extract_company_facts("Kontakt: Musterweg 1, 12345 Berlin")


def test_most_recent_date_rule():
    from app.pipeline.enrichment import most_recent_date

    assert most_recent_date("1919", "1947-11-27") == "1947-11-27"
    assert most_recent_date(None, "1990-06-04", "1987-12-16") == "1990-06-04"
    assert most_recent_date("garbage", None) is None
    assert most_recent_date("20.02.2019", "2018-01-01") == "2019-02-20"


def test_registry_overrides_brand_founding_date_and_legal_form():
    # Agent answer carried Wikidata-flavoured values (brand founding 1919,
    # English legal-form gloss); the register record must replace both.
    row = _row(
        registry_id="00445790",
        name_normalized_register_name="TESCO PLC",
        incorporation_date="1919",
        organization_type="public limited company",
    )
    registry_rec = _record(
        registry_id="00445790", provider="companies_house",
        incorporation_date="1947-11-27", organization_type="Public limited company",
        registry_court=None,
    )
    merged = _merge_from_records(row, matching_records(row, [registry_rec]))
    assert merged.incorporation_date == "1947-11-27"  # registry wins, even though older year given first
    assert merged.organization_type == "Public limited company"

    # Without a registry source, conflicting dates resolve to the most recent.
    row2 = _row(registry_id="X1", name_normalized_register_name="Acme", incorporation_date="1919")
    wiki_rec = _record(registry_id="X1", provider="wikidata", incorporation_date="1947-11-27")
    merged2 = _merge_from_records(row2, matching_records(row2, [wiki_rec]))
    assert merged2.incorporation_date == "1947-11-27"


def test_registry_court_comes_from_register_record():
    row = _row(registry_id="HRB 1", name_normalized_register_name="Acme GmbH")
    rec = _record(registry_id="HRB 1", provider="handelsregister",
                  registry_court="Amtsgericht München")
    merged = _merge_from_records(row, matching_records(row, [rec]))
    assert merged.registry_court == "Amtsgericht München"


def test_scrub_kills_json_fragment_values():
    from app.pipeline.enrichment import _scrub

    # The ': null,' web-payload leak (and friends) must never reach the CSV.
    row = _row(
        registry_id="HRB 1", name_normalized_register_name="Acme GmbH",
        registered_address=": null,", organization_type="null", status="N/A",
        officers="  ", incorporation_date="2019-02-20",
    )
    out = _scrub(row)
    assert out.registered_address is None
    assert out.organization_type is None
    assert out.status is None
    assert out.officers is None
    assert out.incorporation_date == "2019-02-20"  # real value untouched
    assert out.registry_id == "HRB 1"


def test_scrub_blanks_true_no_match_but_keeps_identified_no_id():
    from app.pipeline.enrichment import _scrub

    # True no-match: no id AND no name -> everything blank.
    nomatch = _row(
        no_match_reason="not_in_registry", registry_id=None,
        name_normalized_register_name=None, registered_address=": null,",
        organization_type="AG", status="active", source="some-non-url-ref",
    )
    out = _scrub(nomatch)
    assert out.registered_address is None and out.organization_type is None
    assert out.status is None and out.source is None

    # Identified by name but no registry number -> keep the enrichment.
    ided = _row(
        no_match_reason="id_not_in_sources", registry_id=None,
        name_normalized_register_name="bean ventures GmbH",
        registered_address="Sachseln, CH", organization_type="GmbH",
        status="active",
    )
    out2 = _scrub(ided)
    assert out2.registered_address == "Sachseln, CH"
    assert out2.organization_type == "GmbH" and out2.status == "active"


def test_german_court_collapses_to_amtsgericht_city():
    from app.search.registry_format import normalize_registry_court as n

    assert n("DE", "Bavaria District court München") == "Amtsgericht München"
    assert n("DE", "North Rhine-Westphalia District court Düsseldorf") == "Amtsgericht Düsseldorf"
    assert n("DE", "Baden-Württemberg District court Stuttgart") == "Amtsgericht Stuttgart"
    assert n("DE", "District court München") == "Amtsgericht München"
    assert n("DE", "Local Court Munich") == "Amtsgericht Munich"
    assert n("DE", "Amtsgericht München") == "Amtsgericht München"  # already canonical
    # Non-DE jurisdictions are left alone.
    assert n("PL", "SĄD REJONOWY DLA M.ST. WARSZAWY") == "SĄD REJONOWY DLA M.ST. WARSZAWY"
