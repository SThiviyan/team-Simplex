"""MCP client connections for the pipeline agent.

Two transports behind one context manager, so the agent always speaks real MCP
to "the correct endpoint for the correct country":

- `internal:<bucket>` — in-memory transport straight to our own per-country
  FastMCP instance (app/mcp_servers/country_endpoints.py). Works in the CLI,
  tests, and the deployed container without requiring an HTTP round-trip.
- `http(s)://...` — streamable-HTTP client for our deployed /mcp/<bucket>
  endpoints or any third-party MCP server (optional bearer auth).
"""

import json
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.memory import create_connected_server_and_client_session

from app.mcp_servers.country_endpoints import server_for_bucket

INTERNAL_SCHEME = "internal:"


@asynccontextmanager
async def open_session(url: str, auth_token: str | None = None):
    """Yield an initialized MCP ClientSession for an MCP-registry entry URL."""
    if url.startswith(INTERNAL_SCHEME):
        bucket = url[len(INTERNAL_SCHEME) :]
        server = server_for_bucket(bucket)
        if server is None:
            raise ValueError(f"no internal MCP bucket {bucket!r}")
        async with create_connected_server_and_client_session(server) as session:
            yield session
    else:
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
        async with streamablehttp_client(url, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def anthropic_tools(list_tools_result) -> list[dict]:
    """MCP tool descriptors -> Anthropic `tools` parameter entries."""
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
        }
        for t in list_tools_result.tools
    ]


async def call_tool(session: ClientSession, name: str, arguments: dict) -> tuple[str, object]:
    """Call an MCP tool; return (text for the model, parsed JSON for the trace)."""
    result = await session.call_tool(name, arguments)
    parts = [c.text for c in result.content if getattr(c, "text", None)]
    text = "\n".join(parts) if parts else "{}"
    if result.isError:
        return f"Tool error: {text}", None
    structured = result.structuredContent
    if structured is None:
        try:
            structured = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            structured = None
    return text, structured
