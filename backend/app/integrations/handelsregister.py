"""Client for the German Handelsregister, with a fallback.

Primary path: the handelsregister.ai JSON API (fast, structured), keyed with an
`x-api-key` header. Fallback path: scrape the official handelsregister.de JSF
portal like a browser (mechanize + BeautifulSoup) when no API key is configured
or the API call fails. Both paths return the same normalised row shape.

Docs: https://handelsregister.ai/en/documentation

Shared by the company-registry MCP server (via the provider) and the
HandelsregisterSearchProvider so request/normalisation logic lives in one place.
"""

import asyncio
import logging
import re

import certifi
import httpx
import mechanize
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- handelsregister.ai API (primary) --------------------------------------

API_BASE = "https://handelsregister.ai/api/v1"


def _registry_id(reg: dict) -> str | None:
    """Combine the register section + number, e.g. "HRB" + "42243" -> "HRB 42243"."""
    rtype, rnum = reg.get("register_type"), reg.get("register_number")
    if rtype and rnum:
        return f"{rtype} {rnum}"
    return rnum or None


def _court(court: str | None) -> str | None:
    """The register's court — prefix the local-court form when given a bare city
    ("München" -> "Amtsgericht München")."""
    if not court:
        return None
    return court if court.lower().startswith("amtsgericht") else f"Amtsgericht {court}"


def _api_address(a: dict) -> str | None:
    line = " ".join(p for p in (a.get("street"), a.get("house_number")) if p)
    parts = [
        line or None,
        " ".join(p for p in (a.get("postal_code"), a.get("city")) if p) or None,
        a.get("state"),
    ]
    return ", ".join(p for p in parts if p) or None


def _normalise_api(r: dict) -> dict:
    reg = r.get("registration") or {}
    addr = r.get("address") or {}
    return {
        "number": _registry_id(reg),
        "name": r.get("name"),
        "legal_form": None,  # not in the search result; the registered name carries it
        "city": addr.get("city"),
        "address": _api_address(addr),
        "status": None,  # not in the search result
        "incorporation_date": r.get("registration_date"),
        "country": "DE",
        "court": _court(reg.get("court")),
        "url": None,
    }


async def _api_search(name: str, api_key: str, limit: int = 10) -> list[dict]:
    """Search via the handelsregister.ai API. Raises on any HTTP/transport error."""
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    params = {"q": name, "page": 1}
    async with httpx.AsyncClient(base_url=API_BASE, headers=headers, timeout=30.0) as client:
        resp = await client.get("/search-organizations", params=params)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [_normalise_api(r) for r in results[:limit]]


# --- handelsregister.de portal scraper (fallback) --------------------------

START_URL = "https://www.handelsregister.de"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/15.5 Safari/605.1.15"
)
_REG_RE = re.compile(r"(HRA|HRB|GnR|VR|PR)\s*\d+(\s+[A-Z]{1,2})?")


def _browser() -> mechanize.Browser:
    b = mechanize.Browser()
    b.set_handle_robots(False)
    b.set_handle_equiv(True)
    b.set_handle_refresh(False)
    try:
        b.set_ca_data(cafile=certifi.where())  # real CA bundle for TLS
    except Exception:
        pass
    b.addheaders = [
        ("User-Agent", _UA),
        ("Accept-Language", "en-GB,en;q=0.9"),
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    ]
    return b


def _scrape_search(name: str, limit: int = 10) -> list[dict]:
    """Keyword search of the German commercial register by scraping the portal.

    Synchronous and slow — call off the event loop (asyncio.to_thread).
    """
    b = _browser()
    b.open(START_URL, timeout=25)

    # Trigger the JSF "advanced search" command link by injecting its params.
    b.select_form(name="naviForm")
    b.form.new_control(
        "hidden", "naviForm:erweiterteSucheLink", {"value": "naviForm:erweiterteSucheLink"}
    )
    b.form.new_control("hidden", "target", {"value": "erweiterteSucheLink"})
    b.submit()

    # Option "1" = "must contain all keywords" — works with a bare keyword.
    b.select_form(name="form")
    b["form:schlagwoerter"] = name
    b["form:schlagwortOptionen"] = ["1"]
    html = b.submit().read().decode("utf-8")

    grid = BeautifulSoup(html, "html.parser").find("table", role="grid")
    if grid is None:
        return []

    results: list[dict] = []
    for row in grid.find_all("tr"):
        if row.get("data-ri") is None:  # only actual company rows
            continue
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) < 3:
            continue
        location = cells[1]  # "Bundesland District-court City <reg-no>"
        m = _REG_RE.search(location)
        court = re.sub(r"\s+", " ", _REG_RE.sub("", location)).strip()
        results.append(
            {
                "number": m.group(0) if m else None,
                "name": cells[2],
                "legal_form": None,
                "city": None,
                "address": None,
                "status": cells[4] if len(cells) > 4 else None,
                "incorporation_date": None,
                "country": "DE",
                "court": court or None,
                "url": None,
            }
        )
        if len(results) >= limit:
            break
    return results


# --- public entry point: API first, scrape on failure / no key -------------


async def search_companies(
    name: str, api_key: str | None = None, limit: int = 10
) -> list[dict]:
    """Search the German register. Use the handelsregister.ai API when a key is
    configured; fall back to scraping handelsregister.de when there is no key or
    the API call is not working (auth/HTTP/transport error)."""
    if api_key:
        try:
            return await _api_search(name, api_key, limit)
        except Exception as exc:
            logger.warning(
                "handelsregister.ai API not working (%s); falling back to the website scraper",
                exc,
            )
    # The browser scrape is ~50s/query and usually empty — only run it when
    # explicitly opted in (settings.handelsregister_scrape_fallback).
    from app.config import settings

    if not settings.handelsregister_scrape_fallback:
        logger.info(
            "handelsregister: no API key and scrape fallback disabled; returning no results"
        )
        return []
    return await asyncio.to_thread(_scrape_search, name, limit)
