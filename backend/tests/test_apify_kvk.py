"""Tests for the Apify KvK (NL) name-search provider. Actor call is mocked."""

from app.config import settings
from app.integrations import apify_kvk
from app.search.csv_search import select_providers
from app.search.providers.apify_kvk import ApifyKvkSearchProvider
from app.search.sources import all_providers

_ITEM = {
    "kvkNumber": "17085815",
    "establishmentNumber": "000014503441",
    "name": "ASML Holding",
    "legalForm": "Naamloze Vennootschap",
    "isActive": True,
    "registrationType": "Hoofdvestiging",
    "visitingAddress": {"street": "De Run", "houseNumber": "6501", "postalCode": "5504DR", "city": "Veldhoven"},
    "currentTradeNames": ["ASML Holding"],
    "activityDescription": "Het oprichten van vennootschappen ...",
}


def test_to_row_maps_kvk_item():
    row = apify_kvk.to_row(_ITEM)
    assert row["number"] == "17085815"
    assert row["name"] == "ASML Holding"
    assert row["legal_form"] == "Naamloze Vennootschap"
    assert row["country"] == "NL"
    assert row["city"] == "Veldhoven"
    assert "De Run 6501" in row["address"]
    assert row["status"] is None  # active
    assert row["metadata"]["is_active"] is True


def test_to_row_inactive_marks_dissolved():
    row = apify_kvk.to_row({**_ITEM, "isActive": False})
    assert row["status"] == "dissolved"


async def test_search_companies_name_query(monkeypatch):
    async def fake(actor, run_input, token, *, timeout=90.0):
        assert actor == apify_kvk.ACTOR
        assert run_input["searchQuery"] == "ASML"
        return [_ITEM]

    monkeypatch.setattr("app.integrations.apify_kvk.run_actor_get_items", fake)
    rows = await apify_kvk.search_companies("ASML", "tok")
    assert rows and rows[0]["name"] == "ASML Holding"


async def test_provider_disabled_without_flag(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", False)
    assert await ApifyKvkSearchProvider().search("ASML") == []


async def test_provider_enabled_maps_results(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def fake(query, token, *, limit=10):
        return [apify_kvk.to_row(_ITEM)]

    monkeypatch.setattr("app.integrations.apify_kvk.search_companies", fake)
    results = await ApifyKvkSearchProvider().search("ASML")
    assert results
    r = results[0]
    assert r.source == "apify_kvk_nl"
    assert r.jurisdiction == "NL"
    assert r.registry_id == "17085815"
    assert r.organization_type == "Naamloze Vennootschap"


async def test_provider_degrades_on_error(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def boom(*a, **k):
        raise RuntimeError("apify down")

    monkeypatch.setattr("app.integrations.apify_kvk.search_companies", boom)
    assert await ApifyKvkSearchProvider().search("ASML") == []


def test_kvk_registered_and_scoped_to_nl():
    provs = all_providers()
    assert "apify_kvk_nl" in {p.name for p in provs}
    assert any(p.name == "apify_kvk_nl" for p in select_providers(provs, "NL"))
    assert all(p.name != "apify_kvk_nl" for p in select_providers(provs, "DE"))
