"""Tests for the CSV-driven search: parsing, jurisdiction-based source skipping."""

from app.search.base import SearchProvider, SearchResult
from app.search.csv_search import csv_search, parse_query_csv, select_providers

_SCHEMA_FIELDS = {
    "query_id",
    "registry_id",
    "registry_court",
    "name_normalized_register_name",
    "jurisdiction_confirmed",
    "confidence",
    "source",
    "no_match_reason",
}


class _Fake(SearchProvider):
    def __init__(self, name, jurisdictions=None, results=None):
        self.name = name
        self.jurisdictions = jurisdictions
        self._results = results or []

    async def search(self, query, limit=10):
        return self._results


def _providers():
    return [
        _Fake("gleif"),  # global
        _Fake("wikidata"),  # global
        _Fake("handelsregister", {"DE"}),
        _Fake("brreg", {"NO"}),
        _Fake("annuaire", {"FR"}),
    ]


def test_parse_csv():
    assert parse_query_csv("name,jurisdiction\nTesla,US") == [("Tesla", "US")]
    assert parse_query_csv("Equinor, NO") == [("Equinor", "NO")]
    assert parse_query_csv("Tesla") == [("Tesla", None)]


def test_parse_space_separated_jurisdiction():
    # "Tesla DE" (no comma) -> name "Tesla", jurisdiction "DE".
    assert parse_query_csv("TESLA DE") == [("TESLA", "DE")]
    assert parse_query_csv("Equinor NO") == [("Equinor", "NO")]
    # Multi-word names keep working; only a trailing ISO code is split off.
    assert parse_query_csv("Deutsche Bank DE") == [("Deutsche Bank", "DE")]
    # Legal-form codes are NOT treated as jurisdictions (use a comma instead).
    assert parse_query_csv("BMW AG") == [("BMW AG", None)]
    assert parse_query_csv("Volvo SE") == [("Volvo SE", None)]


def test_handelsregister_provider_sets_jurisdiction():
    from app.search.providers.handelsregister import HandelsregisterSearchProvider

    assert HandelsregisterSearchProvider().jurisdictions == {"DE"}


def test_skip_registers_that_cannot_match_jurisdiction():
    p = _providers()
    # Hungary: no national register matches -> all registers skipped, globals kept.
    names = {x.name for x in select_providers(p, "HU")}
    assert names == {"gleif", "wikidata"}
    # Germany: only the German register is called among the national ones.
    de = {x.name for x in select_providers(p, "DE")}
    assert de == {"gleif", "wikidata", "handelsregister"}


def test_no_jurisdiction_uses_global_sources_only():
    # Without a country we stay on the worldwide sources: nothing becomes
    # unreachable (GLEIF/Wikidata cover every jurisdiction) and the query
    # doesn't fan out to a dozen irrelevant national registers.
    p = _providers()
    assert {x.name for x in select_providers(p, None)} == {"gleif", "wikidata"}


async def test_csv_search_reports_skipped_sources():
    out = await csv_search(_providers(), "Tesla,HU")
    q = out["queries"][0]
    assert q["query_id"] == "q1"
    assert set(q["sources_called"]) == {"gleif", "wikidata"}
    assert {"handelsregister", "brreg", "annuaire"} == set(q["sources_skipped"])


async def test_output_record_has_required_schema():
    hit = SearchResult(
        title="Sinpex GmbH (LEI123)",
        snippet="",
        score=0.9,
        source="gleif",
        jurisdiction="DE",
        registry_id="LEI123",
        register_name="Sinpex GmbH",
    )
    out = await csv_search([_Fake("gleif", None, [hit])], "Sinpex,DE")
    rec = out["results"][0]
    assert _SCHEMA_FIELDS <= set(rec)  # every required field present
    assert rec["query_id"] == "q1"
    assert rec["registry_id"] == "LEI123"
    assert rec["name_normalized_register_name"] == "Sinpex GmbH"
    assert rec["jurisdiction_confirmed"] == "DE"
    assert 0.0 <= rec["confidence"] <= 1.0
    assert rec["no_match_reason"] is None


async def test_no_match_record_emitted():
    out = await csv_search([_Fake("gleif", None, [])], "Nonexistent,DE")
    rec = out["results"][0]
    assert _SCHEMA_FIELDS <= set(rec)
    assert rec["query_id"] == "q1"
    assert rec["registry_id"] is None
    assert rec["name_normalized_register_name"] is None
    assert rec["no_match_reason"] == "not_in_registry"
