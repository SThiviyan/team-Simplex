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
import time

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


class _Throttle:
    """Spaces requests `min_interval` apart (process-wide per integration), so
    a high-concurrency batch queues politely at a rate-limited upstream
    instead of burning its quota on 429s."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._next_slot = 0.0
        self._lock: asyncio.Lock | None = None
        self._lock_loop: int | None = None

    def _get_lock(self) -> asyncio.Lock:
        loop_id = id(asyncio.get_running_loop())
        if self._lock is None or self._lock_loop != loop_id:
            self._lock = asyncio.Lock()
            self._lock_loop = loop_id
        return self._lock

    async def wait(self) -> None:
        async with self._get_lock():
            now = time.monotonic()
            delay = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self.min_interval
        if delay > 0:
            await asyncio.sleep(delay)


_throttles: dict[str, _Throttle] = {}


async def rate_limited_get(
    name: str,
    client: httpx.AsyncClient,
    url: str,
    *,
    min_interval: float,
    max_attempts: int = 4,
    **request_kwargs,
) -> httpx.Response:
    """client.get with request spacing and 429 retry (honours Retry-After).

    Responses are byte-identical to a plain get — this only changes WHEN the
    request is sent, never what is sent.
    """
    throttle = _throttles.setdefault(name, _Throttle(min_interval))
    throttle.min_interval = min_interval  # honour the caller's current value
    for attempt in range(max_attempts):
        await throttle.wait()
        resp = await client.get(url, **request_kwargs)
        if resp.status_code != 429 or attempt == max_attempts - 1:
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            pause = min(float(retry_after), 30.0) if retry_after else 2.0 * (attempt + 1)
        except ValueError:
            pause = 2.0 * (attempt + 1)
        await asyncio.sleep(pause)
    return resp  # pragma: no cover — loop always returns
