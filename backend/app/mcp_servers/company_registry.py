"""Unified company-registry MCP server.

A single MCP server that fronts every data source (GLEIF, Wikidata, and the
national registers DE/NO/FR/DK/IE/US/FI). It can query all of them at once with
jurisdiction-aware routing, or hit a single source directly.

Run as a stdio MCP server:  python -m app.mcp_servers.company_registry

Tools:
  - search_companies:  search all relevant registers; if a jurisdiction is
                       given, only sources that can match it are queried and
                       results are filtered to that jurisdiction.
  - search_one_source: query a single named source's API directly.
  - list_sources:      list available sources and the jurisdictions they cover.
"""

from mcp.server.fastmcp import FastMCP

from app.search.csv_search import search_jurisdiction
from app.search.sources import all_providers

mcp = FastMCP("company-registry")

# Built once; provider construction does no network I/O.
_PROVIDERS = all_providers()
_BY_NAME = {p.name: p for p in _PROVIDERS}


@mcp.tool()
async def search_companies(name: str, jurisdiction: str | None = None, limit: int = 10) -> dict:
    """Search company registers by name across all relevant sources.

    Args:
        name: Company name to search for.
        jurisdiction: Optional ISO 3166-1 alpha-2 code (e.g. "DE", "US", "FI").
            When given, only sources that can match it are queried and results
            are filtered to that jurisdiction; otherwise every source is queried.
        limit: Max results per source.

    Returns `{sources_called, sources_skipped, results}` — the results being a
    merged, jurisdiction-filtered list of company records.
    """
    results, called, skipped = await search_jurisdiction(_PROVIDERS, name, jurisdiction, limit)
    return {
        "sources_called": called,
        "sources_skipped": skipped,
        "results": [r.model_dump() for r in results],
    }


@mcp.tool()
async def search_one_source(source: str, name: str, limit: int = 10) -> list[dict]:
    """Query one source's API directly by name.

    Args:
        source: Source id — one of: gleif, wikidata, handelsregister,
            companies_house, brreg, annuaire, cvr, cro, sec, prh, ares,
            ariregister, rpo, orgbook, rasham, rsk, kvk.
        name: Company name to search for.
        limit: Max results.
    """
    provider = _BY_NAME.get(source)
    if provider is None:
        return []
    try:
        return [r.model_dump() for r in await provider.search(name, limit=limit)]
    except Exception:
        return []


@mcp.tool()
def list_sources() -> list[dict]:
    """List the available sources and the jurisdictions each covers.

    `jurisdictions` is null for global sources (always relevant) or a list of
    ISO 3166-1 alpha-2 codes for jurisdiction-scoped registers.
    """
    return [
        {
            "name": p.name,
            "jurisdictions": sorted(p.jurisdictions) if p.jurisdictions else None,
        }
        for p in _PROVIDERS
    ]


if __name__ == "__main__":
    mcp.run()
