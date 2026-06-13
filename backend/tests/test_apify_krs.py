"""Tests for the Apify-backed Poland KRS provider (key-gated, by KRS number).

All tests monkeypatch the actor call — they never run the real Apify actor.
"""

from app.config import settings
from app.integrations import apify_krs
from app.search.csv_search import select_providers
from app.search.providers.apify_krs import ApifyKrsSearchProvider
from app.search.sources import all_providers

_ITEM = {
    "krsNumber": "0000028860",
    "name": "ORLEN SPÓŁKA AKCYJNA",
    "legalForm": "SPÓŁKA AKCYJNA",
    "nip": "7740001454",
    "regon": "61018820100000",
    "registrationDate": "19.07.2001",
    "lastUpdateDate": "07.04.2026",
    "city": "PŁOCK",
    "street": "CHEMIKÓW",
    "houseNumber": "7",
    "postalCode": "09-411",
    "shareCapital": "1 783 509 563,00 ZŁ",
    "pkdCodes": ["19.20.Z"],
    "directors": [{"name": "A"}, {"name": "B"}],
    "isBankrupt": False,
    "inLiquidation": False,
}


# --- mapping ------------------------------------------------------------------

def test_to_row_maps_rich_fields():
    row = apify_krs.to_row(_ITEM)
    assert row["number"] == "0000028860"
    assert row["name"] == "ORLEN SPÓŁKA AKCYJNA"
    assert row["country"] == "PL"
    assert row["incorporation_date"] == "2001-07-19"
    assert row["last_update"] == "2026-04-07"
    assert "CHEMIKÓW 7" in row["address"]
    md = row["metadata"]
    assert md["nip"] == "7740001454"
    assert md["director_count"] == 2
    assert md["pkd_codes"] == ["19.20.Z"]
    assert md["in_liquidation"] is False


def test_status_flags():
    assert apify_krs._status({"inLiquidation": True}) == "in_liquidation"
    assert apify_krs._status({"isBankrupt": True}) == "dissolved"
    assert apify_krs._status({}) is None


# --- integration (actor call monkeypatched) -----------------------------------

async def test_name_only_query_does_not_call_the_actor(monkeypatch):
    calls = {"n": 0}

    async def fake(actor, run_input, token, *, timeout=90.0):
        calls["n"] += 1
        return []

    monkeypatch.setattr("app.integrations.apify_krs.run_actor_get_items", fake)
    assert await apify_krs.search_companies("Orlen", "tok") == []
    assert calls["n"] == 0  # no KRS number -> no actor run (no compute spent)


async def test_search_by_krs_number_runs_actor_and_maps(monkeypatch):
    async def fake(actor, run_input, token, *, timeout=90.0):
        assert actor == apify_krs.ACTOR
        assert run_input["krsNumbers"] == ["0000028860"]
        assert run_input["registry"] == "P"
        return [_ITEM]

    monkeypatch.setattr("app.integrations.apify_krs.run_actor_get_items", fake)
    rows = await apify_krs.search_companies("KRS 0000028860", "tok")
    assert rows and rows[0]["name"] == "ORLEN SPÓŁKA AKCYJNA"


# --- provider -----------------------------------------------------------------

async def test_provider_self_disables_without_token(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", None)
    monkeypatch.setattr(settings, "apify_enabled", True)
    assert await ApifyKrsSearchProvider().search("KRS 0000028860") == []


async def test_provider_disabled_without_global_flag(monkeypatch):
    # Token present but APIFY_ENABLED is off -> self-disables (no actor call).
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", False)
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        return []

    monkeypatch.setattr("app.integrations.apify_krs.search_companies", fake)
    assert await ApifyKrsSearchProvider().search("KRS 0000028860") == []
    assert calls["n"] == 0


async def test_provider_maps_with_token(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def fake(query, token, *, limit=10, timeout=90.0):
        return [apify_krs.to_row(_ITEM)]

    monkeypatch.setattr("app.integrations.apify_krs.search_companies", fake)
    results = await ApifyKrsSearchProvider().search("KRS 0000028860")
    assert results
    r = results[0]
    assert r.jurisdiction == "PL"
    assert r.registry_id == "0000028860"
    assert r.source == "apify_krs_pl"
    assert r.metadata.get("nip") == "7740001454"
    assert "NIP" in r.snippet


async def test_provider_degrades_on_error(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def boom(query, token, *, limit=10, timeout=90.0):
        raise RuntimeError("apify down")

    monkeypatch.setattr("app.integrations.apify_krs.search_companies", boom)
    assert await ApifyKrsSearchProvider().search("KRS 0000028860") == []


def test_apify_krs_registered_and_scoped_to_pl():
    provs = all_providers()
    assert "apify_krs_pl" in {p.name for p in provs}
    assert any(p.name == "apify_krs_pl" for p in select_providers(provs, "PL"))
    assert all(p.name != "apify_krs_pl" for p in select_providers(provs, "DE"))
