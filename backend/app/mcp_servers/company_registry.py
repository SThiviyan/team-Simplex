"""Unified company-registry MCP server.

A single MCP server fronting every data source — global identifiers (GLEIF,
Wikidata, OpenCorporates), national registers (DE/GB/FR/ES/NL/US/JP/BR/PL/…), and
premium Apify-actor sources. With this many APIs, the tools route by jurisdiction
and cost so a caller picks the right calls instead of fanning out to everything.

Run as a stdio MCP server:  python -m app.mcp_servers.company_registry

Tools:
  - recommend_sources: given a jurisdiction, which sources to call and in what
                       order (free official registers first, premium last).
  - search_companies:  search the relevant sources for a jurisdiction (free
                       sources by default; set include_premium=True to add the
                       paid Apify ones).
  - search_one_source: query a single named source directly.
  - list_sources:      every source with its jurisdiction + routing metadata.
"""

from mcp.server.fastmcp import FastMCP

from app.search.csv_search import search_jurisdiction
from app.search.routing import describe, has_identifier, rank_providers
from app.search.sources import all_providers

mcp = FastMCP("company-registry")

# Built once; provider construction does no network I/O.
_PROVIDERS = all_providers()
_BY_NAME = {p.name: p for p in _PROVIDERS}


@mcp.tool()
def recommend_sources(
    jurisdiction: str | None = None, has_registration_number: bool = False
) -> dict:
    """Recommend which sources to call for a jurisdiction, best-first.

    Helps choose the right calls from the large catalog. Returns the relevant
    sources ranked (free official registers first, then global, then premium
    paid sources), split into ``free`` and ``premium``. Number-only sources
    (CNPJ/KRS/VAT lookups) are included only when ``has_registration_number`` is
    true, since they cannot match a plain name.

    Args:
        jurisdiction: ISO 3166-1 alpha-2 code (e.g. "DE", "US", "FR"); None = any.
        has_registration_number: set true if the query carries an official
            registration / VAT / CNPJ / KRS number.
    """
    ranked = rank_providers(
        _PROVIDERS, jurisdiction, include_premium=True, identifier_present=has_registration_number
    )
    free = [describe(p) for p in ranked if p.cost == "free"]
    premium = [describe(p) for p in ranked if p.cost == "premium"]
    return {
        "jurisdiction": jurisdiction,
        "ranked": [describe(p) for p in ranked],
        "free": free,
        "premium": premium,
        "note": (
            "Call the free sources first. Premium (Apify) sources are paid and "
            "opt-in (require APIFY_ENABLED); use them only when free sources are "
            "insufficient."
        ),
    }


@mcp.tool()
async def search_companies(
    name: str,
    jurisdiction: str | None = None,
    limit: int = 10,
    include_premium: bool = False,
) -> dict:
    """Search company registers by name across the relevant sources.

    Routes by jurisdiction and cost: only sources that can match the jurisdiction
    are queried, free ones by default. Set ``include_premium=True`` to also query
    the paid Apify-backed sources. Results are merged and jurisdiction-filtered.

    Args:
        name: Company name (or a name carrying a registration number).
        jurisdiction: Optional ISO 3166-1 alpha-2 code; when given, only matching
            sources are queried and results are filtered to it.
        limit: Max results per source.
        include_premium: Include paid Apify sources (NorthData, OpenCorporates, …).

    Returns `{sources_called, sources_skipped, routing, results}`.
    """
    selected = rank_providers(
        _PROVIDERS,
        jurisdiction,
        include_premium=include_premium,
        identifier_present=has_identifier(name),
    )
    results, called, skipped = await search_jurisdiction(selected, name, jurisdiction, limit)
    return {
        "sources_called": called,
        "sources_skipped": skipped,
        "routing": {
            "include_premium": include_premium,
            "selected": [p.name for p in selected],
        },
        "results": [r.model_dump() for r in results],
    }


@mcp.tool()
async def search_one_source(source: str, name: str, limit: int = 10) -> list[dict]:
    """Query one source's API directly by name. Use `list_sources` for source ids."""
    provider = _BY_NAME.get(source)
    if provider is None:
        return []
    try:
        return [r.model_dump() for r in await provider.search(name, limit=limit)]
    except Exception:
        return []


@mcp.tool()
def list_sources() -> list[dict]:
    """List every source with its jurisdictions and routing metadata.

    Each entry: `{name, jurisdictions, tier, cost, lookup}` — `jurisdictions` is
    null for global sources; `cost` is "free" or "premium" (paid Apify); `lookup`
    is "name" (name search) or "number" (id/number lookup only).
    """
    return [describe(p) for p in _PROVIDERS]


if __name__ == "__main__":
    mcp.run()
