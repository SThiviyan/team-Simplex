"""Tests for the additional Europe services: Poland KRS provider + scrape registries."""

import pytest

from app.integrations import krs_pl
from app.integrations.krs_pl import _to_iso, _to_row, extract_krs
from app.pipeline.mcp_registry import get_mcp_servers
from app.search.csv_search import select_providers
from app.search.providers.krs_pl import KrsPlSearchProvider
from app.search.sources import all_providers

_ODPIS = {
    "naglowekA": {"numerKRS": "0000028860", "dataRejestracjiWKRS": "19.07.2001"},
    "dane": {
        "dzial1": {
            "danePodmiotu": {"nazwa": "ORLEN SPÓŁKA AKCYJNA", "formaPrawna": "SPÓŁKA AKCYJNA"},
            "siedzibaIAdres": {
                "siedziba": {"miejscowosc": "PŁOCK"},
                "adres": {
                    "ulica": "CHEMIKÓW",
                    "nrDomu": "7",
                    "kodPocztowy": "09-411",
                    "miejscowosc": "PŁOCK",
                    "kraj": "POLSKA",
                },
            },
        }
    },
}


# --- KRS parsing + mapping ----------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("0000028860", "0000028860"),
        ("KRS 0000028860", "0000028860"),
        ("KRS: 0000028860 (Orlen)", "0000028860"),
        ("Order 12 KRS 0000028860", "0000028860"),
        ("Orlen", None),
        ("12345", None),
    ],
)
def test_extract_krs(text, expected):
    assert extract_krs(text) == expected


def test_to_iso_converts_polish_date():
    assert _to_iso("19.07.2001") == "2001-07-19"
    assert _to_iso(None) is None


def test_to_row_maps_krs_excerpt():
    row = _to_row(_ODPIS)
    assert row["number"] == "0000028860"
    assert row["name"] == "ORLEN SPÓŁKA AKCYJNA"
    assert row["legal_form"] == "SPÓŁKA AKCYJNA"
    assert row["city"] == "PŁOCK"
    assert row["country"] == "PL"
    assert row["incorporation_date"] == "2001-07-19"
    assert "CHEMIKÓW 7" in row["address"]


async def test_search_companies_name_only_makes_no_request():
    assert await krs_pl.search_companies("Orlen") == []


# --- provider -----------------------------------------------------------------

async def test_provider_maps_krs_hit_to_search_result(monkeypatch):
    async def fake(query, limit=10):
        return [_to_row(_ODPIS)]

    monkeypatch.setattr("app.integrations.krs_pl.search_companies", fake)
    results = await KrsPlSearchProvider().search("KRS 0000028860")
    assert results
    r = results[0]
    assert r.jurisdiction == "PL"
    assert r.registry_id == "0000028860"
    assert r.source == "krs_pl"


async def test_provider_degrades_gracefully_on_error(monkeypatch):
    async def boom(query, limit=10):
        raise RuntimeError("api down")

    monkeypatch.setattr("app.integrations.krs_pl.search_companies", boom)
    assert await KrsPlSearchProvider().search("KRS 0000028860") == []


def test_krs_registered_and_scoped_to_pl():
    provs = all_providers()
    assert "krs_pl" in {p.name for p in provs}
    assert any(p.name == "krs_pl" for p in select_providers(provs, "PL"))
    assert all(p.name != "krs_pl" for p in select_providers(provs, "DE"))


# --- Europe scrape registries -------------------------------------------------

@pytest.mark.parametrize(
    "cc,first",
    [
        ("ES", "registro-mercantil"),
        ("IT", "registro-imprese"),
        ("SE", "bolagsverket"),
        ("BE", "kbo-bce"),
        ("AT", "firmenbuch"),
        ("CH", "zefix"),
        ("PT", "publicacoes-mj"),
        ("PL", "krs-wyszukiwarka"),
    ],
)
def test_europe_scrape_lists(cc, first):
    entries = get_mcp_servers(cc)
    assert entries and entries[0].name == first
    assert all(e.kind == "scrape" for e in entries)
    assert all(not e.is_placeholder for e in entries)
    assert all(e.domain for e in entries)
    assert [e.rank for e in entries] == sorted(e.rank for e in entries)
