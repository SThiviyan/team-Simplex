"""Client for Poland's KRS (Krajowy Rejestr Sądowy) court register.

Free, keyless. The Ministry of Justice publishes the current excerpt by KRS
number: ``GET https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}?rejestr={P|S}
&format=json``. There is no public name search, so this resolves a company when
the query carries a 10-digit KRS number (KYC inputs frequently do); a pure-name
query yields nothing here and is left to GLEIF / the scrape layer.

Docs: https://prs.ms.gov.pl/krs/openApi
"""

import re

import httpx

API = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny"
_UA = "team-simplex-hackathon/1.0 (company search demo)"
# P = rejestr przedsiębiorców (companies), S = rejestr stowarzyszeń (associations).
_REGISTERS = ("P", "S")


def extract_krs(text: str) -> str | None:
    """Return the 10-digit KRS number found in ``text`` (zero-padded), or None."""
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10:
        return digits
    m = re.search(r"\b\d{10}\b", text)
    return m.group(0) if m else None


def _to_iso(pl_date: str | None) -> str | None:
    """Convert a Polish ``DD.MM.YYYY`` date to ISO ``YYYY-MM-DD``."""
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", pl_date or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else (pl_date or None)


def _to_row(odpis: dict) -> dict:
    """Map a KRS ``odpis`` (excerpt) to the shared register-row shape."""
    nag = odpis.get("naglowekA") or {}
    dzial1 = (odpis.get("dane") or {}).get("dzial1") or {}
    podmiot = dzial1.get("danePodmiotu") or {}
    sia = dzial1.get("siedzibaIAdres") or {}
    adres = sia.get("adres") or {}
    siedziba = sia.get("siedziba") or {}
    krs = nag.get("numerKRS")
    address = ", ".join(
        str(x)
        for x in (
            " ".join(str(p) for p in (adres.get("ulica"), adres.get("nrDomu")) if p),
            adres.get("kodPocztowy"),
            adres.get("miejscowosc"),
        )
        if x
    )
    return {
        "number": krs,
        "name": podmiot.get("nazwa"),
        "legal_form": podmiot.get("formaPrawna"),
        "city": siedziba.get("miejscowosc") or adres.get("miejscowosc"),
        "status": None,
        "incorporation_date": _to_iso(nag.get("dataRejestracjiWKRS")),
        "country": "PL",
        "address": address or None,
        "url": f"{API}/{krs}?rejestr=P&format=json" if krs else None,
    }


async def search_companies(query: str, limit: int = 10) -> list[dict]:
    """Resolve a Polish company by the KRS number embedded in the query (if any)."""
    krs = extract_krs(query)
    if not krs:
        return []  # no public name search — covered by GLEIF / the scrape layer
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        for rejestr in _REGISTERS:
            resp = await client.get(f"{API}/{krs}", params={"rejestr": rejestr, "format": "json"})
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            odpis = (resp.json() or {}).get("odpis")
            if odpis and (odpis.get("dane") or {}).get("dzial1"):
                row = _to_row(odpis)
                if row.get("name"):
                    return [row]
    return []
