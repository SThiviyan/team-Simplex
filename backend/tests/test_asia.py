"""Tests for the Asia services: Japan gBizINFO provider + scrape registries."""

import pytest

from app.config import settings
from app.integrations import gbizinfo
from app.pipeline.mcp_registry import get_mcp_servers
from app.search.csv_search import select_providers
from app.search.providers.gbizinfo import GbizInfoSearchProvider
from app.search.sources import all_providers

_SAMPLE = {
    "corporate_number": "5010001000001",
    "name": "トヨタ自動車株式会社",
    "location": "愛知県豊田市トヨタ町1番地",
    "kind": "301",
    "date_of_establishment": "1937-08-28",
    "close_date": None,
}


# --- gBizINFO mapping ----------------------------------------------------------

def test_to_row_maps_gbizinfo_entry():
    row = gbizinfo._to_row(_SAMPLE)
    assert row["number"] == "5010001000001"
    assert row["name"] == "トヨタ自動車株式会社"
    assert row["legal_form"] == "株式会社"  # kind 301
    assert row["country"] == "JP"
    assert row["status"] is None  # no close_date
    assert "info.gbiz.go.jp" in row["url"]


def test_to_row_marks_closed_entities():
    row = gbizinfo._to_row({**_SAMPLE, "close_date": "2020-03-31"})
    assert row["status"] == "closed"


# --- provider (token-gated, self-disabling) -----------------------------------

async def test_provider_self_disables_without_token(monkeypatch):
    monkeypatch.setattr(settings, "gbizinfo_api_token", None)
    assert await GbizInfoSearchProvider().search("トヨタ") == []


async def test_provider_maps_results_with_token(monkeypatch):
    monkeypatch.setattr(settings, "gbizinfo_api_token", "tok")

    async def fake(name, token, limit=10):
        assert token == "tok"
        return [gbizinfo._to_row(_SAMPLE)]

    monkeypatch.setattr("app.integrations.gbizinfo.search_companies", fake)
    results = await GbizInfoSearchProvider().search("トヨタ")
    assert results
    r = results[0]
    assert r.jurisdiction == "JP"
    assert r.registry_id == "5010001000001"
    assert r.source == "gbizinfo"


async def test_provider_degrades_on_error(monkeypatch):
    monkeypatch.setattr(settings, "gbizinfo_api_token", "tok")

    async def boom(name, token, limit=10):
        raise RuntimeError("api down")

    monkeypatch.setattr("app.integrations.gbizinfo.search_companies", boom)
    assert await GbizInfoSearchProvider().search("トヨタ") == []


def test_gbizinfo_registered_and_scoped_to_jp():
    provs = all_providers()
    assert "gbizinfo" in {p.name for p in provs}
    assert any(p.name == "gbizinfo" for p in select_providers(provs, "JP"))
    assert all(p.name != "gbizinfo" for p in select_providers(provs, "DE"))


# --- Asia scrape registries ---------------------------------------------------

@pytest.mark.parametrize(
    "cc,first",
    [
        ("IN", "mca-master-data"),
        ("CN", "gsxt"),
        ("JP", "gbizinfo"),
        ("KR", "dart"),
        ("SG", "acra-bizfile"),
        ("HK", "icris"),
        ("TW", "findbiz"),
        ("ID", "ahu"),
    ],
)
def test_asia_scrape_lists(cc, first):
    entries = get_mcp_servers(cc)
    assert entries and entries[0].name == first
    assert all(e.kind == "scrape" for e in entries)
    assert all(not e.is_placeholder for e in entries)  # real registry sites
    assert all(e.domain for e in entries)
    assert [e.rank for e in entries] == sorted(e.rank for e in entries)
