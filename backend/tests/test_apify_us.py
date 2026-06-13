"""Tests for the Apify US business-entity provider. Actor call is mocked."""

from app.config import settings
from app.integrations import apify_us
from app.search.csv_search import select_providers
from app.search.providers.apify_us import ApifyUsSearchProvider
from app.search.sources import all_providers

_ITEM = {
    "entityName": "TESLA INC",
    "entityId": "0805242630",
    "state": "TX",
    "address": {"street": "1 Tesla Road", "city": "Austin", "zip": "78725"},
    "sourceUrl": "https://comptroller.texas.gov/data-search/franchise-tax/0805242630",
    "searchQuery": "Tesla",
}


def test_to_row_maps_us_item():
    row = apify_us.to_row(_ITEM)
    assert row["number"] == "0805242630"
    assert row["name"] == "TESLA INC"
    assert row["country"] == "US"
    assert row["court"] == "TX Secretary of State"
    assert row["metadata"]["state"] == "TX"
    assert "Austin" in row["address"]
    assert row["url"].startswith("https://comptroller.texas.gov/")


def test_to_row_skips_junk_zip_in_address():
    # A junk zip (e.g. 'CANADA') must not leak into the address (it would mislead
    # jurisdiction inference). Only street/city are composed.
    row = apify_us.to_row({**_ITEM, "address": {"zip": "CANADA"}})
    assert row["address"] is None


async def test_search_companies_passes_states_and_query(monkeypatch):
    async def fake(actor, run_input, token, *, timeout=90.0):
        assert actor == apify_us.ACTOR
        assert run_input["searchQuery"] == "Tesla"
        assert run_input["states"] == apify_us.DEFAULT_STATES
        return [_ITEM]

    monkeypatch.setattr("app.integrations.apify_us.run_actor_get_items", fake)
    rows = await apify_us.search_companies("Tesla", "tok")
    assert rows and rows[0]["name"] == "TESLA INC"


async def test_provider_disabled_without_flag(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", False)
    assert await ApifyUsSearchProvider().search("Tesla") == []


async def test_provider_enabled_maps_results(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def fake(query, token, *, limit=10):
        return [apify_us.to_row(_ITEM)]

    monkeypatch.setattr("app.integrations.apify_us.search_companies", fake)
    results = await ApifyUsSearchProvider().search("Tesla")
    assert results
    r = results[0]
    assert r.source == "apify_us"
    assert r.jurisdiction == "US"
    assert r.registry_id == "0805242630"
    assert r.registry_court == "TX Secretary of State"


async def test_provider_degrades_on_error(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def boom(*a, **k):
        raise RuntimeError("apify down")

    monkeypatch.setattr("app.integrations.apify_us.search_companies", boom)
    assert await ApifyUsSearchProvider().search("Tesla") == []


def test_us_registered_and_scoped_to_us():
    provs = all_providers()
    assert "apify_us" in {p.name for p in provs}
    assert any(p.name == "apify_us" for p in select_providers(provs, "US"))
    assert all(p.name != "apify_us" for p in select_providers(provs, "DE"))
