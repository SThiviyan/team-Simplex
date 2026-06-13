"""Minimal Apify API client — run a Store actor and get its dataset items.

Shared by the optional, key-gated Apify-backed integrations. The caller passes
the token (so this stays free of global config); a missing token is the caller's
concern (the provider self-disables).

run-sync-get-dataset-items runs the actor and returns the dataset in the same
HTTP response — simplest for small, bounded lookups.
"""

import httpx

BASE = "https://api.apify.com/v2"
_UA = "team-simplex-hackathon/1.0 (company search demo)"


async def run_actor_get_items(
    actor: str,
    run_input: dict,
    token: str,
    *,
    timeout: float = 90.0,
) -> list[dict]:
    """Run ``actor`` (the ``username~actorname`` id) synchronously and return its
    dataset items. Raises on HTTP error; returns [] when the dataset is empty."""
    url = f"{BASE}/acts/{actor}/run-sync-get-dataset-items"
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": _UA}) as client:
        resp = await client.post(url, params={"token": token}, json=run_input)
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else []
