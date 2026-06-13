"""Tests for the unified company-registry MCP server."""

import app.mcp_servers.company_registry as cr


async def test_tools_registered():
    names = {t.name for t in await cr.mcp.list_tools()}
    assert {"search_companies", "search_one_source", "list_sources", "recommend_sources"} <= names


def test_list_sources_covers_all_with_jurisdictions():
    by = {s["name"]: s["jurisdictions"] for s in cr.list_sources()}
    assert len(by) == 30
    # Global sources have no jurisdiction restriction.
    assert by["gleif"] is None and by["wikidata"] is None
    # National registers are jurisdiction-scoped.
    assert by["handelsregister"] == ["DE"]
    assert by["companies_house"] == ["GB"]
    assert by["brreg"] == ["NO"]
    assert by["sec"] == ["US"]
    assert by["prh"] == ["FI"]
    assert by["ares"] == ["CZ"]
    assert by["ariregister"] == ["EE"]
    assert by["rpo"] == ["SK"]
    assert by["orgbook"] == ["CA"]
    assert by["rasham"] == ["IL"]
    assert by["rsk"] == ["IS"]
    assert by["kvk"] == ["NL"]
    assert by["brasil_cnpj"] == ["BR"]
    assert by["gbizinfo"] == ["JP"]
    assert by["krs_pl"] == ["PL"]
    assert by["apify_krs_pl"] == ["PL"]
    assert by["northdata"] == ["AT", "CH", "DE"]
    assert by["apify_kvk_nl"] == ["NL"]
    assert by["apify_us"] == ["US"]
    assert by["apify_fr"] == ["FR"]
    assert by["apify_jp_gbiz"] == ["JP"]
    assert by["apify_br"] == ["BR"]
    assert by["apify_es"] == ["ES"]
    assert by["opencorporates"] is None  # global
    assert "DE" in by["apify_eu_vat"] and "US" not in by["apify_eu_vat"]


def test_list_sources_includes_routing_metadata():
    by = {s["name"]: s for s in cr.list_sources()}
    assert by["handelsregister"]["cost"] == "free"
    assert by["northdata"]["cost"] == "premium"
    assert by["gleif"]["tier"] == "global"
    assert by["krs_pl"]["lookup"] == "number"


def test_recommend_sources_routes_by_cost_and_number():
    rec = cr.recommend_sources("DE")
    assert all(s["cost"] == "free" for s in rec["free"])
    assert all(s["cost"] == "premium" for s in rec["premium"])
    assert "handelsregister" in [s["name"] for s in rec["free"]]
    # Without a registration number, number-only sources are not recommended.
    assert "apify_eu_vat" not in [s["name"] for s in rec["ranked"]]
    # With one, number-only lookups appear.
    rec_id = cr.recommend_sources("PL", has_registration_number=True)
    assert "krs_pl" in [s["name"] for s in rec_id["ranked"]]
