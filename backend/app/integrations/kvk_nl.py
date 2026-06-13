"""Client for the KVK API — the Dutch commercial register (Kamer van Koophandel).

The KVK "Zoeken" (search) API needs an API key in the `apikey` header. With KVK's
public TEST key the request goes to the test endpoint (synthetic data); with a
production key (developers.kvk.nl, paid) it goes to the live endpoint and returns
real companies.

Docs: https://developers.kvk.nl/documentation

Shared by the company-registry MCP server (via the provider) and the
KvkNlSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

TEST_KEY = "l7xx1f2691f2520d487b902f4e0b57a0b197"
TEST_BASE = "https://api.kvk.nl/test/api/v2"
PROD_BASE = "https://api.kvk.nl/api/v2"


def _base(api_key: str) -> str:
    """The public test key only works against the test endpoint; any other key
    is a production key and uses the live endpoint."""
    return TEST_BASE if api_key == TEST_KEY else PROD_BASE


def _address(adres: dict | None) -> str | None:
    if not adres:
        return None
    a = adres.get("binnenlandsAdres") or adres.get("buitenlandsAdres") or {}
    parts = [
        " ".join(str(p) for p in (a.get("straatnaam"), a.get("huisnummer")) if p) or None,
        " ".join(str(p) for p in (a.get("postcode"), a.get("plaats")) if p) or None,
        a.get("land"),  # only on foreign (buitenlands) addresses
    ]
    return ", ".join(p for p in parts if p) or None


def _normalise(r: dict) -> dict:
    return {
        "number": r.get("kvkNummer"),  # 8-digit KVK registration number
        "name": r.get("naam"),
        "legal_form": None,  # not in the search result (lives in the basisprofiel)
        "city": ((r.get("adres") or {}).get("binnenlandsAdres") or {}).get("plaats"),
        "address": _address(r.get("adres")),
        "status": None,
        "incorporation_date": None,
        "country": "NL",
        "court": None,
        "url": None,
    }


async def search_companies(name: str, api_key: str, limit: int = 10) -> list[dict]:
    """Search the Dutch register by company name (KVK Zoeken API)."""
    headers = {"apikey": api_key, "Accept": "application/json"}
    params = {"naam": name, "resultatenPerPagina": max(1, min(limit, 100))}
    async with httpx.AsyncClient(
        base_url=_base(api_key), headers=headers, timeout=20.0
    ) as client:
        resp = await client.get("/zoeken", params=params)
        resp.raise_for_status()
        results = resp.json().get("resultaten", [])
        return [_normalise(r) for r in results[:limit]]
