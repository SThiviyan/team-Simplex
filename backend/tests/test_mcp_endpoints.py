"""Per-country MCP endpoints + the agent's MCP client connection."""

from app.mcp_servers.country_endpoints import (
    GLOBAL_BUCKET,
    bucket_for_country,
    country_servers,
    server_for_bucket,
)
from app.pipeline.mcp_client import anthropic_tools, call_tool, open_session

EXPECTED_TOOLS = {"search_companies", "search_one_source", "list_sources"}


def test_buckets_cover_national_providers_plus_global():
    servers = country_servers()
    assert GLOBAL_BUCKET in servers
    # One bucket per jurisdiction a national provider covers.
    assert {"de", "us", "fr", "no", "dk", "ie", "fi"} <= set(servers)


def test_bucket_routing():
    assert bucket_for_country("DE") == "de"
    assert bucket_for_country("de") == "de"
    assert bucket_for_country("US-CA") == "us"  # state code -> parent country
    assert bucket_for_country("UK") == GLOBAL_BUCKET  # no national provider yet
    assert bucket_for_country(None) == GLOBAL_BUCKET
    assert server_for_bucket("nope") is None


async def test_in_memory_mcp_round_trip():
    """The agent's transport: real MCP protocol against a country bucket."""
    async with open_session("internal:de") as session:
        tools = anthropic_tools(await session.list_tools())
        assert {t["name"] for t in tools} == EXPECTED_TOOLS
        assert all(t["input_schema"].get("type") == "object" for t in tools)

        # list_sources needs no network: DE bucket = Handelsregister + globals.
        text, structured = await call_tool(session, "list_sources", {})
        names = {s["name"] for s in structured["result"]} if isinstance(structured, dict) else {
            s["name"] for s in structured
        }
        assert "handelsregister" in names
        assert "gleif" in names


async def test_global_bucket_has_no_national_registers():
    async with open_session("internal:global") as session:
        _, structured = await call_tool(session, "list_sources", {})
        sources = structured["result"] if isinstance(structured, dict) else structured
        assert all(s["jurisdictions"] is None for s in sources)
