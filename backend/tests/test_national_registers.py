"""Tests for the national-register providers.

Hermetic tests cover jurisdiction metadata + routing (no network). Live tests
hit the real keyless APIs and are guarded by RUN_LIVE_TESTS.
"""

import os

import pytest

from app.search.providers.annuaire import AnnuaireSearchProvider
from app.search.providers.brreg import BrregSearchProvider
from app.search.providers.cro import CroSearchProvider
from app.search.providers.cvr import CvrSearchProvider
from app.search.providers.gleif import GleifSearchProvider
from app.search.providers.prh import PrhSearchProvider
from app.search.providers.sec import SecSearchProvider
from app.search.resolver import select_providers


def test_jurisdiction_metadata():
    assert BrregSearchProvider().jurisdictions == {"NO"}
    assert AnnuaireSearchProvider().jurisdictions == {"FR"}
    assert CvrSearchProvider().jurisdictions == {"DK"}
    assert CroSearchProvider().jurisdictions == {"IE"}
    assert SecSearchProvider().jurisdictions == {"US"}
    assert PrhSearchProvider().jurisdictions == {"FI"}
    assert GleifSearchProvider().jurisdictions is None  # global


def test_routing_picks_only_relevant_register():
    providers = [
        GleifSearchProvider(),
        BrregSearchProvider(),
        AnnuaireSearchProvider(),
        CvrSearchProvider(),
        CroSearchProvider(),
    ]
    names = {p.name for p in select_providers(providers, "NO")}
    assert "brreg" in names and "gleif" in names  # NO register + global
    assert {"annuaire", "cvr", "cro"}.isdisjoint(names)  # other registers skipped
    # Ireland routing brings in CRO (keyless) and excludes the others.
    ie = {p.name for p in select_providers(providers, "IE")}
    assert "cro" in ie and {"brreg", "annuaire", "cvr"}.isdisjoint(ie)


def test_cro_is_keyless_and_enabled():
    assert CroSearchProvider().enabled is True


_live = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1 for network tests"
)


@_live
async def test_brreg_live():
    rs = await BrregSearchProvider().search("Equinor", limit=3)
    assert rs and any("EQUINOR" in r.title.upper() for r in rs)
    assert all(r.source == "brreg" for r in rs)


@_live
async def test_annuaire_live():
    rs = await AnnuaireSearchProvider().search("TotalEnergies", limit=3)
    assert rs and all(r.source == "annuaire" for r in rs)


@_live
async def test_cvr_live():
    rs = await CvrSearchProvider().search("Maersk", limit=1)
    assert rs and rs[0].source == "cvr"


@_live
async def test_cro_live_keyless():
    # Keyless via data.gov.ie; "LIMITED" is present in the open extract.
    rs = await CroSearchProvider().search("LIMITED", limit=3)
    assert rs and all(r.source == "cro" for r in rs)


@_live
async def test_sec_live():
    rs = await SecSearchProvider().search("Tesla", limit=3)
    assert rs and rs[0].source == "sec" and rs[0].jurisdiction == "US"
    assert rs[0].registry_id  # CIK present


@_live
async def test_prh_live():
    rs = await PrhSearchProvider().search("Nokia", limit=3)
    assert rs and rs[0].source == "prh" and rs[0].jurisdiction == "FI"


async def test_gleif_maps_national_number_not_lei(monkeypatch):
    """registry_id must be the national register number (registeredAs); the LEI
    is a cross-reference and belongs in metadata."""
    from app.integrations import gleif as gleif_integration

    async def fake_search(name, limit=10):
        return [
            {
                "lei": "2138002P5RNKC5W2JZ46",
                "registered_as": "00445790",
                "registration_authority": "RA000585",
                "name": "TESCO PLC",
                "country": "GB",
                "record_url": "https://search.gleif.org/#/record/2138002P5RNKC5W2JZ46",
            },
            # No national number published -> registry_id stays blank, not the LEI.
            {"lei": "5299000HTNHA5ACH9N32", "registered_as": None, "name": "Red Bull GmbH"},
        ]

    monkeypatch.setattr(gleif_integration, "search_entities", fake_search)
    results = await GleifSearchProvider().search("Tesco", limit=5)

    assert results[0].registry_id == "00445790"
    assert results[0].metadata["lei"] == "2138002P5RNKC5W2JZ46"
    assert results[0].metadata["registration_authority"] == "RA000585"
    assert results[1].registry_id is None
    assert results[1].metadata["lei"] == "5299000HTNHA5ACH9N32"


def test_gleif_registered_as_drops_placeholders():
    from app.integrations.gleif import _registered_as

    assert _registered_as({"registeredAs": "00445790"}) == "00445790"
    assert _registered_as({"registeredAs": " HRB 12345 "}) == "HRB 12345"
    assert _registered_as({"registeredAs": "n/a"}) is None
    assert _registered_as({"registeredAs": "----"}) is None
    assert _registered_as({"registeredAs": "0000"}) is None
    assert _registered_as({}) is None


async def test_sec_demotes_cik_to_metadata(monkeypatch):
    """A CIK is an EDGAR index key, not a registration number."""
    from app.integrations import sec as sec_integration

    async def fake_search(name, limit=10):
        return [
            {
                "number": "0000320193",
                "name": "Apple Inc.",
                "legal_form": "ticker AAPL",
                "city": None,
                "status": None,
                "country": "US",
                "court": None,
                "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193",
            }
        ]

    monkeypatch.setattr(sec_integration, "search_companies", fake_search)
    results = await SecSearchProvider().search("Apple", limit=5)
    assert results[0].registry_id is None
    assert results[0].metadata["cik"] == "0000320193"


def test_pulled_mcp_connectors_metadata_and_wiring():
    from app.search.providers.ariregister import AriregisterSearchProvider
    from app.search.providers.kvk_nl import KvkNlSearchProvider
    from app.search.providers.orgbook_ca import OrgbookCaSearchProvider
    from app.search.providers.rasham_il import RashamIlSearchProvider
    from app.search.providers.rpo_sk import RpoSkSearchProvider
    from app.search.providers.rsk_is import RskIsSearchProvider
    from app.search.sources import all_providers

    assert AriregisterSearchProvider().jurisdictions == {"EE"}
    assert RpoSkSearchProvider().jurisdictions == {"SK"}
    assert OrgbookCaSearchProvider().jurisdictions == {"CA"}
    assert RashamIlSearchProvider().jurisdictions == {"IL"}
    assert RskIsSearchProvider().jurisdictions == {"IS"}
    assert KvkNlSearchProvider().jurisdictions == {"NL"}

    names = {p.name for p in all_providers()}
    assert {"ariregister", "rpo", "orgbook", "rasham", "rsk", "kvk"} <= names
    # verify_info MCP integrations were pulled in too (keyless + premium/opt-in).
    assert {"krs_pl", "brasil_cnpj", "northdata", "apify_us"} <= names
    assert len(all_providers()) >= 20


async def test_kvk_self_disables_without_key(monkeypatch):
    from app.config import settings
    from app.search.providers.kvk_nl import KvkNlSearchProvider

    monkeypatch.setattr(settings, "kvk_api_key", None)
    assert await KvkNlSearchProvider().search("Shell", limit=3) == []


async def test_handelsregister_scrape_respects_opt_out(monkeypatch):
    from app.config import settings
    from app.search.providers.handelsregister import HandelsregisterSearchProvider

    monkeypatch.setattr(settings, "handelsregister_scrape_fallback", False)
    # Off -> returns [] without ever touching the (slow) scraper.
    assert await HandelsregisterSearchProvider().search("Siemens", limit=3) == []


async def test_northdata_costs_nothing_unless_apify_enabled(monkeypatch):
    """Cost guard: the PAID NorthData/Apify actor must NEVER be invoked unless
    BOTH a token is set AND apify_enabled is true (prod-only). The €5 budget
    depends on this — dev (apify_enabled=false) must self-skip with no actor call.
    """
    from app.config import settings
    from app.integrations import apify_northdata
    from app.search.providers.northdata import NorthDataSearchProvider

    called = False

    async def _boom(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("paid Apify actor was invoked while disabled")

    monkeypatch.setattr(apify_northdata, "search_companies", _boom)

    # Even WITH a token, apify_enabled=false -> skip, no actor call.
    monkeypatch.setattr(settings, "apify_api_key", "tok_xxx")
    monkeypatch.setattr(settings, "apify_enabled", False)
    assert await NorthDataSearchProvider().search("BMW", limit=3) == []
    # And with no token at all.
    monkeypatch.setattr(settings, "apify_api_key", None)
    monkeypatch.setattr(settings, "apify_enabled", True)
    assert await NorthDataSearchProvider().search("BMW", limit=3) == []
    assert called is False  # the actor was never reached in either case
