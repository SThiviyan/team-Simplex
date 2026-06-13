"""Tests for the 6 added Apify providers (FR/JP/BR/ES/EU-VAT/OpenCorporates).

Actor calls are mocked; these check the row mappings, opt-in gating, and that
each provider is registered with the right jurisdiction + routing metadata.
"""

from app.config import settings
from app.integrations import (
    apify_br,
    apify_es,
    apify_fr,
    apify_jp_gbiz,
    apify_opencorporates,
    apify_vat,
)
from app.search.csv_search import select_providers
from app.search.providers.apify_fr import ApifyFrSearchProvider
from app.search.sources import all_providers


# --- row mappings -------------------------------------------------------------

def test_fr_to_row():
    row = apify_fr.to_row(
        {"siren": "503932568", "nom_complet": "CARREFOUR", "date_creation": "2007-01-01",
         "etat_administratif": "A", "nature_juridique": "9110", "ville": "LE CANNET",
         "adresse": "1 AV", "libelle_activite_principale": "Immobilier", "departement_nom": "AM"}
    )
    assert row["number"] == "503932568" and row["name"] == "CARREFOUR"
    assert row["country"] == "FR" and row["status"] is None and row["city"] == "LE CANNET"
    assert row["incorporation_date"] == "2007-01-01"


def test_jp_to_row():
    row = apify_jp_gbiz.to_row(
        {"corporate_number": "1180301018771", "name": "トヨタ自動車株式会社",
         "location": "愛知県豊田市", "capital_stock": 635402000000, "employee_number": 83533,
         "date_of_establishment": "1937-08-28", "update_date": "2026-01-01"}
    )
    assert row["number"] == "1180301018771" and row["country"] == "JP"
    assert row["address"] == "愛知県豊田市"
    assert row["metadata"]["capital_stock"] == 635402000000


def test_br_to_row():
    row = apify_br.to_row(
        {"cnpj": "33000167000101", "razao_social": "PETROBRAS", "natureza_juridica": "SA",
         "municipio": "RIO DE JANEIRO", "uf": "RJ", "situacao_cadastral": "ATIVA",
         "data_abertura": "1966-09-28", "logradouro": "AV CHILE", "numero": "65"}
    )
    assert row["number"] == "33000167000101" and row["country"] == "BR"
    assert row["status"] is None  # ATIVA
    assert row["incorporation_date"] == "1966-09-28"
    assert "AV CHILE 65" in row["address"]


def test_es_to_row():
    row = apify_es.to_row(
        {"companyName": "TELEFONICA SA", "nif": "A12345678", "legalForm": "SA",
         "status": "Vigente", "address": "Madrid", "province": "MADRID",
         "registroMercantil": "MADRID", "euid": "ES123"}
    )
    assert row["number"] == "A12345678" and row["country"] == "ES"
    assert row["status"] is None and row["court"] == "MADRID"


def test_vat_extract_and_row():
    assert apify_vat.extract_vat("VAT NL004495445B01 here") == "NL004495445B01"
    assert apify_vat.extract_vat("Google") is None
    row = apify_vat.to_row(
        {"valid": True, "vatNumber": "NL004495445B01", "countryCode": "NL",
         "name": "ACME BV", "address": "Delft", "riskLevel": "low", "countryReliability": "high"}
    )
    assert row["number"] == "NL004495445B01" and row["country"] == "NL" and row["name"] == "ACME BV"
    # invalid VAT -> dropped
    assert apify_vat.to_row({"valid": False, "vatNumber": "X"}) is None


def test_opencorporates_to_row():
    row = apify_opencorporates.to_row(
        {"companyName": "GOOGLE LLC", "companyNumber": "06770815", "jurisdictionCode": "us_de",
         "jurisdiction": "US", "incorporationDate": "1998-09-04", "companyType": "llc",
         "status": "active", "registeredAddress": "CA", "registryUrl": "https://x"}
    )
    assert row["number"] == "06770815" and row["country"] == "US"
    assert row["status"] == "active" and row["url"] == "https://x"


# --- gating + registration ----------------------------------------------------

async def test_fr_provider_self_disables_without_flag(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", False)
    assert await ApifyFrSearchProvider().search("Carrefour") == []


async def test_fr_provider_enabled_maps(monkeypatch):
    monkeypatch.setattr(settings, "apify_api_key", "tok")
    monkeypatch.setattr(settings, "apify_enabled", True)

    async def fake(query, token, *, limit=10):
        return [apify_fr.to_row({"siren": "1", "nom_complet": "ACME SA", "etat_administratif": "A"})]

    monkeypatch.setattr("app.integrations.apify_fr.search_companies", fake)
    res = await ApifyFrSearchProvider().search("ACME")
    assert res and res[0].jurisdiction == "FR" and res[0].source == "apify_fr"


def test_new_providers_registered_and_scoped():
    provs = all_providers()
    names = {p.name for p in provs}
    for n in ("apify_fr", "apify_jp_gbiz", "apify_br", "apify_es", "apify_eu_vat", "opencorporates"):
        assert n in names
    assert any(p.name == "apify_fr" for p in select_providers(provs, "FR"))
    assert any(p.name == "apify_jp_gbiz" for p in select_providers(provs, "JP"))
    assert any(p.name == "apify_es" for p in select_providers(provs, "ES"))
    # OpenCorporates is global -> relevant to every jurisdiction.
    assert any(p.name == "opencorporates" for p in select_providers(provs, "ZA"))
    # EU VAT covers EU members but not the US.
    assert all(p.name != "apify_eu_vat" for p in select_providers(provs, "US"))
