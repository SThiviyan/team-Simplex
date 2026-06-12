"""Scraper client for the official German Handelsregister (handelsregister.de).

No JSON API exists, so we drive the JSF portal like a browser (mechanize):
open the start page for a session, trigger the "advanced search" navigation,
submit the keyword form, parse the results grid. Synchronous and slow — call
it off the event loop (asyncio.to_thread). Shared by the MCP server and the
HandelsregisterSearchProvider.
"""

import re

import certifi
import mechanize
from bs4 import BeautifulSoup

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


def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Keyword search of the German commercial register. Returns row dicts."""
    b = _browser()
    b.open(START_URL, timeout=25)

    # Trigger the JSF "advanced search" command link by injecting its params.
    b.select_form(name="naviForm")
    b.form.new_control("hidden", "naviForm:erweiterteSucheLink", {"value": "naviForm:erweiterteSucheLink"})
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
        location = cells[1]  # "Bundesland District court City <reg-no>"
        m = _REG_RE.search(location)
        # The court is the location minus the register number (kept separately).
        court = re.sub(r"\s+", " ", _REG_RE.sub("", location)).strip()
        results.append(
            {
                "name": cells[2],
                "court": court,
                "register_number": m.group(0) if m else None,
                "status": cells[4] if len(cells) > 4 else None,
                "jurisdiction": "DE",
            }
        )
        if len(results) >= limit:
            break
    return results
