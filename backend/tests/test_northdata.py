"""Tests for the NorthData (Apify) name-search provider. Actor call is mocked."""

from app.config import settings
from app.integrations import apify_northdata
from app.search.csv_search import select_providers
from app.search.providers.northdata import NorthDataSearchProvider
from app.search.sources import all_providers

_ITEM = {
    "dataId": "5020826785808384",
    "name": "Siemens N.V., Beersel, Belgium",
    "detailPageUrl": "https://www.northdata.com/Siemens/KBO%200404.284.716",
    "summary": "Crossroads Bank for Enterprises (KBO) 0404.284.716",
    "siren": None,
    "lat": 50.75,
    "lng": 4.27,
    "dataDetails": {"nameScore": 500, "score": 122.7},
}


# --- parsing ------------------------------------------------------------------

def test_search_url_encodes_name():
    assert apify_northdata.search_url("Siemens AG") == (
        "https://www.northdata.com/_search?query=Siemens%20AG"
    )


def test_split_name_extracts_company_city_country():
    assert apify_northdata._split_name("Siemens N.V., Beersel, Belgium") == (
        "Siemens N.V.", "Beersel", "BE"
    )
    assert apify_northdata._split_name("ACME GmbH, München, Germany") == (
        "ACME GmbH", "München", "DE"
    )
    assert apify_northdata._split_name("Loner") == ("Loner", None, None)
    # NorthData sometimes uses an ISO code instead of a full country name.
    assert apify_northdata._split_name("Siemens, Norderstedt, DE") == ("Siemens", "Norderstedt", "DE")


def test_parse_summary_pulls_registration_number():
    rid, court = apify_northdata._parse_summary("Crossroads Bank for Enterprises (KBO) 0404.284.716")
    assert rid == "0404.284.716"
    assert "Crossroads" in court
    rid2, _ = apify_northdata._parse_summary("Commercial Register Munich HRB 6684")
    assert rid2 == "HRB 6684"


def test_to_row_maps_item():
    row = apify_northdata.to_row(_ITEM)
    assert row["name"] == "Siemens N.V."
    assert row["country"] == "BE"
    assert row["number"] == "0404.284.716"
    assert row["url"].startswith("https://www.northdata.com/")
    assert row["metadata"]["name_score"] == 500


# --- integration (actor mocked) -----------------------------------------------

async def test_search_companies_runs_actor_and_maps(monkeypatch):
    async def fake(actor, run_input, token, *, timeout=120.0):
        assert actor == apify_northdata.ACTOR
        assert run_input["searchUrl"].endswith("query=Siemens")
        return [_ITEM]

    monkeypatch.setattr("app.integrations.apify_northdata.run_actor_get_items", fake)
    rows = await apify_northdata.search_companies("Siemens", "tok")
    assert rows and rows[0]["name"] == "Siemens N.V."


# --- provider (opt-in gating) -------------------------------------------------

async def test_provider_disabled_without_flag(monkeypatch):
    # Token present but the enable flag is off -> self-disables (no actor call).
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", False)
    called = {"n": 0}

    async def fake(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr("app.integrations.apify_northdata.run_actor_get_items", fake)
    assert await NorthDataSearchProvider().search("Siemens") == []
    assert called["n"] == 0


async def test_provider_disabled_without_token(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", None)
    monkeypatch.setattr(settings, "apify_enabled", True)
    assert await NorthDataSearchProvider().search("Siemens") == []


async def test_provider_enabled_maps_results(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def fake(query, token, *, limit=10):
        return [apify_northdata.to_row(_ITEM)]

    monkeypatch.setattr("app.integrations.apify_northdata.search_companies", fake)
    results = await NorthDataSearchProvider().search("Siemens")
    assert results
    r = results[0]
    assert r.source == "northdata"
    assert r.jurisdiction == "BE"
    assert r.registry_id == "0404.284.716"


async def test_provider_degrades_on_error(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def boom(*a, **k):
        raise RuntimeError("apify down")

    monkeypatch.setattr("app.integrations.apify_northdata.search_companies", boom)
    assert await NorthDataSearchProvider().search("Siemens") == []


def test_northdata_registered_and_scoped_to_dach():
    provs = all_providers()
    assert "northdata" in {p.name for p in provs}
    for cc in ("DE", "AT", "CH"):
        assert any(p.name == "northdata" for p in select_providers(provs, cc))
    assert all(p.name != "northdata" for p in select_providers(provs, "US"))
