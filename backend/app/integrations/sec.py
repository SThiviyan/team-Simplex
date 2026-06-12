"""Client for the U.S. SEC EDGAR register (public companies). Keyless.

Uses SEC's public `company_tickers.json` (CIK + ticker + legal name) for a
name search. Covers SEC-registered (public) US companies. The SEC requires a
descriptive User-Agent. Cached in-process so it's fetched at most once.
"""

import httpx

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
WEB = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK="
# SEC's fair-access policy requires a descriptive User-Agent WITH a contact email.
_UA = "Sinpex Hackathon Demo admin@sinpex-demo.example.com"

_cache: dict = {}


async def _load() -> list[dict]:
    if _cache.get("rows") is None:
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": _UA}) as client:
            resp = await client.get(TICKERS_URL)
            resp.raise_for_status()
            _cache["rows"] = list(resp.json().values())
    return _cache["rows"]


def _rank(query: str, title: str) -> int:
    q, t = query.lower(), title.lower()
    if t == q:
        return 0
    if t.startswith(q):
        return 1
    return 2


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search SEC-registered US public companies by legal name."""
    q = name.strip().lower()
    if not q:
        return []
    matches = [e for e in await _load() if q in (e.get("title") or "").lower()]
    matches.sort(key=lambda e: (_rank(name, e.get("title", "")), len(e.get("title", ""))))

    out: list[dict] = []
    for e in matches[:limit]:
        cik = e.get("cik_str")
        cik10 = f"{cik:010d}" if isinstance(cik, int) else str(cik)
        ticker = e.get("ticker")
        out.append(
            {
                "number": cik10,  # SEC Central Index Key (CIK)
                "name": e.get("title"),
                "legal_form": f"ticker {ticker}" if ticker else None,
                "city": None,
                "status": None,
                "country": "US",
                "court": None,
                "url": f"{WEB}{cik10}" if cik is not None else None,
            }
        )
    return out
