"""Tests for the unified company-registry MCP server."""

import app.mcp_servers.company_registry as cr


async def test_tools_registered():
    names = {t.name for t in await cr.mcp.list_tools()}
    assert {"search_companies", "search_one_source", "list_sources"} <= names


def test_list_sources_covers_all_with_jurisdictions():
    by = {s["name"]: s["jurisdictions"] for s in cr.list_sources()}
    assert len(by) == 17
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
