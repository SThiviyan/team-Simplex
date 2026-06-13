"""Tests for the South America services: Brazil CNPJ provider + scrape registries."""

import pytest

from app.integrations import brasil
from app.integrations.brasil import _to_row, extract_cnpj
from app.pipeline.mcp_registry import get_mcp_servers
from app.search.csv_search import select_providers
from app.search.providers.brasil import BrasilSearchProvider
from app.search.sources import all_providers

_SAMPLE = {
    "cnpj": "33.000.167/0001-01",
    "razao_social": "PETROLEO BRASILEIRO S A PETROBRAS",
    "nome_fantasia": "PETROBRAS",
    "natureza_juridica": "Sociedade Anônima Aberta",
    "municipio": "RIO DE JANEIRO",
    "uf": "RJ",
    "descricao_situacao_cadastral": "ATIVA",
    "data_inicio_atividade": "1966-09-30",
    "descricao_tipo_de_logradouro": "AVENIDA",
    "logradouro": "REPUBLICA DO CHILE",
    "numero": "65",
    "bairro": "CENTRO",
    "cep": "20031912",
}


# --- CNPJ parsing + mapping ---------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("33.000.167/0001-01", "33000167000101"),
        ("33000167000101", "33000167000101"),
        ("Petrobras CNPJ 33.000.167/0001-01", "33000167000101"),
        ("Order 7 ref 33.000.167/0001-01 ok", "33000167000101"),
        ("Petrobras", None),
        ("12345", None),
    ],
)
def test_extract_cnpj(text, expected):
    assert extract_cnpj(text) == expected


def test_to_row_maps_brasilapi_response():
    row = _to_row(_SAMPLE)
    assert row["number"] == "33000167000101"
    assert row["name"].startswith("PETROLEO BRASILEIRO")
    assert row["country"] == "BR"
    assert row["legal_form"] == "Sociedade Anônima Aberta"
    assert row["status"] == "ATIVA"
    assert "RIO DE JANEIRO" in row["address"]


async def test_search_companies_name_only_makes_no_request():
    # No CNPJ in the query -> returns [] without any network call.
    assert await brasil.search_companies("Petrobras") == []


# --- provider -----------------------------------------------------------------

async def test_provider_maps_cnpj_hit_to_search_result(monkeypatch):
    async def fake(query, limit=10):
        return [_to_row(_SAMPLE)]

    monkeypatch.setattr("app.integrations.brasil.search_companies", fake)
    results = await BrasilSearchProvider().search("33.000.167/0001-01")
    assert results
    r = results[0]
    assert r.jurisdiction == "BR"
    assert r.registry_id == "33000167000101"
    assert r.source == "brasil_cnpj"


async def test_provider_degrades_gracefully_on_error(monkeypatch):
    async def boom(query, limit=10):
        raise RuntimeError("api down")

    monkeypatch.setattr("app.integrations.brasil.search_companies", boom)
    assert await BrasilSearchProvider().search("33.000.167/0001-01") == []


def test_brasil_registered_and_scoped_to_br():
    provs = all_providers()
    assert "brasil_cnpj" in {p.name for p in provs}
    assert any(p.name == "brasil_cnpj" for p in select_providers(provs, "BR"))
    # not called for an unrelated jurisdiction
    assert all(p.name != "brasil_cnpj" for p in select_providers(provs, "DE"))


# --- South America scrape registries ------------------------------------------

@pytest.mark.parametrize(
    "cc,first",
    [
        ("BR", "receita-cnpj"),
        ("AR", "afip-padron"),
        ("CL", "registro-empresas"),
        ("CO", "rues"),
        ("PE", "sunat-ruc"),
    ],
)
def test_south_america_scrape_lists(cc, first):
    entries = get_mcp_servers(cc)
    assert entries and entries[0].name == first
    assert all(e.kind == "scrape" for e in entries)
    assert all(not e.is_placeholder for e in entries)  # real registry sites
    assert all(e.domain for e in entries)
    assert [e.rank for e in entries] == sorted(e.rank for e in entries)
