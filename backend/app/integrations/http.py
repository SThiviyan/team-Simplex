"""Shared httpx clients for the registry integrations.

Creating an AsyncClient per search pays TCP + TLS setup on every call. These
helpers keep one long-lived client per (integration, event loop) so connections
are pooled and reused — the requests and responses are byte-identical to the
per-call version, only the handshakes disappear.

Keyed by event loop because httpx pools bind connections to the loop they were
created on: the CLI, the server, and each test create their own loops, and
reusing a pool across loops raises 'attached to a different loop' errors.
"""

import asyncio

import httpx

_clients: dict[tuple[str, int], httpx.AsyncClient] = {}


def shared_client(name: str, **client_kwargs) -> httpx.AsyncClient:
    """Return the long-lived client for `name` on the current event loop."""
    key = (name, id(asyncio.get_running_loop()))
    client = _clients.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(**client_kwargs)
        _clients[key] = client
        # Old loops' clients are tiny (config + closed sockets); cap anyway.
        if len(_clients) > 64:
            _clients.pop(next(iter(_clients)))
    return client
