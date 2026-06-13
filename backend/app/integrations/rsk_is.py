"""Scraper for Iceland's company register (fyrirtækjaskrá, skatturinn.is).

There is no JSON API; a plain GET name search returns an HTML results table
(Kennitala / Nafn / Póstfang), parsed with BeautifulSoup. A single async httpx
GET — no session/JS, so no browser automation needed.

Shared by the company-registry MCP server (via the provider) and the
RskIsSearchProvider so request/parse logic lives in one place.
"""

import httpx
from bs4 import BeautifulSoup

SEARCH_URL = "https://www.skatturinn.is/fyrirtaekjaskra/leit/"
WEB = "https://www.skatturinn.is"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/15.5 Safari/605.1.15"
)


def _kennitala(raw: str) -> str | None:
    """Format the 10-digit Icelandic registration number as "DDMMYY-NNNC"."""
    k = (raw or "").replace("-", "").strip()
    if len(k) == 10 and k.isdigit():
        return f"{k[:6]}-{k[6:]}"
    return k or None


def _parse(html: str, limit: int) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = next(
        (
            t
            for t in soup.find_all("table")
            if any("Kennitala" in th.get_text() for th in t.find_all("th"))
        ),
        None,
    )
    if table is None:
        return []
    rows: list[dict] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        name = tds[1].get_text(" ", strip=True)
        if not name:
            continue
        link = tr.find("a")
        rows.append(
            {
                "number": _kennitala(tds[0].get_text(strip=True)),  # kennitala
                "name": name,
                "legal_form": None,  # the name carries it (ehf. / hf. / sf.)
                "city": None,
                "address": tds[2].get_text(" ", strip=True) or None,
                "status": None,
                "incorporation_date": None,
                "country": "IS",
                "court": None,
                "url": f"{WEB}{link['href']}" if link and link.get("href") else None,
            }
        )
        if len(rows) >= limit:
            break
    return rows


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Icelandic company register by name (scrapes the results page)."""
    async with httpx.AsyncClient(
        timeout=25.0, headers={"User-Agent": _UA}, follow_redirects=True
    ) as client:
        resp = await client.get(SEARCH_URL, params={"nafn": name})
        resp.raise_for_status()
        return _parse(resp.text, limit)
