"""Per-country MCP endpoints — one MCP server per jurisdiction bucket.

This realizes the architecture's per-country "Listen an Quellen": the pipeline
agent (and any external MCP client) connects to the endpoint for the query's
country code and only sees that country's registers plus the global sources.

Buckets are derived from the provider catalogue (`app.search.sources`): every
jurisdiction a national provider covers gets its own server (DE, NO, FR, DK,
IE, US, FI, ...), plus a `global` bucket (GLEIF + Wikidata only) for countries
without a national provider yet (e.g. UK until a Companies House provider
exists).

Deployed, each server is mounted in the FastAPI app at /mcp/<bucket> via
streamable HTTP (see main.py). In-process (CLI, tests), the same FastMCP
instances are reached over the in-memory transport (app/pipeline/mcp_client.py)
— the same MCP protocol either way. The colleague's unified stdio server
(company_registry.py) is unchanged and remains available for Claude Desktop.
"""

from functools import cache

from mcp.server.fastmcp import FastMCP

from app.search.base import SearchProvider
from app.search.csv_search import search_jurisdiction
from app.search.sources import all_providers

GLOBAL_BUCKET = "global"


def _bucket_providers(providers: list[SearchProvider], cc: str | None) -> list[SearchProvider]:
    """Global providers always; the country's national providers when cc is given."""
    if cc is None:
        return [p for p in providers if p.jurisdictions is None]
    return [p for p in providers if p.jurisdictions is None or cc in p.jurisdictions]


def _build_server(providers: list[SearchProvider], cc: str | None) -> FastMCP:
    bucket = (cc or GLOBAL_BUCKET).lower()
    scoped = _bucket_providers(providers, cc)
    by_name = {p.name: p for p in scoped}
    source_names = ", ".join(sorted(by_name))

    server = FastMCP(
        f"company-registry-{bucket}",
        instructions=(
            f"Company-register search for jurisdiction bucket '{bucket}'. "
            f"Available sources: {source_names}."
        ),
        stateless_http=True,
        streamable_http_path="/",
    )

    @server.tool(
        description=(
            f"Search the company registers of the '{bucket}' bucket by name "
            f"(sources: {source_names}). Returns sources_called, sources_skipped "
            "and a merged list of company records (registry_id, registry_court, "
            "register_name, jurisdiction, url, ...)."
        )
    )
    async def search_companies(name: str, limit: int = 10) -> dict:
        results, called, skipped = await search_jurisdiction(scoped, name, cc, limit)
        return {
            "sources_called": called,
            "sources_skipped": skipped,
            "results": [r.model_dump() for r in results],
        }

    @server.tool(
        description=(
            f"Query one source of the '{bucket}' bucket directly by company name. "
            f"Valid source ids: {source_names}."
        )
    )
    async def search_one_source(source: str, name: str, limit: int = 10) -> list[dict]:
        provider = by_name.get(source)
        if provider is None:
            return []
        try:
            return [r.model_dump() for r in await provider.search(name, limit=limit)]
        except Exception:
            # Provider failures are non-fatal — same philosophy as FederatedSearch.
            return []

    @server.tool(
        description=f"List the sources available in the '{bucket}' bucket and their jurisdictions."
    )
    def list_sources() -> list[dict]:
        return [
            {
                "name": p.name,
                "jurisdictions": sorted(p.jurisdictions) if p.jurisdictions else None,
            }
            for p in scoped
        ]

    return server


@cache
def country_servers() -> dict[str, FastMCP]:
    """Bucket label (lowercase) -> FastMCP server. Built once per process."""
    providers = all_providers()
    covered: set[str] = set()
    for p in providers:
        if p.jurisdictions:
            covered |= p.jurisdictions

    servers = {cc.lower(): _build_server(providers, cc) for cc in sorted(covered)}
    servers[GLOBAL_BUCKET] = _build_server(providers, None)
    return servers


def server_for_bucket(bucket: str) -> FastMCP | None:
    return country_servers().get(bucket.lower())


def bucket_for_country(country_code: str | None) -> str:
    """Country code -> bucket label; falls back to the global bucket."""
    cc = (country_code or "").strip().upper()
    # State/province inputs like "US-CA" route to the parent country's bucket.
    base = cc.split("-")[0]
    servers = country_servers()
    if base.lower() in servers:
        return base.lower()
    return GLOBAL_BUCKET
