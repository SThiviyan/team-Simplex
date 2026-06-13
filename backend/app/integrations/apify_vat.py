"""EU VAT (VIES) validation/enrichment via the Apify 'ryanclinton/eu-vat-validator'
actor. Resolves a VAT number to the official registered name/address. By VAT
number only (the query must carry one). Opt-in.
"""

import re

from app.integrations.apify import run_actor_get_items

ACTOR = "ryanclinton~eu-vat-validator"

# EU member-state VAT formats (+ EL=Greece alt prefix, XI=Northern Ireland).
_VAT_RE = re.compile(
    r"\b("
    r"ATU\d{8}|BE0?\d{9,10}|BG\d{9,10}|HR\d{11}|CY\d{8}[A-Z]|CZ\d{8,10}|DE\d{9}|DK\d{8}|"
    r"EE\d{9}|EL\d{9}|GR\d{9}|ES[A-Z0-9]\d{7}[A-Z0-9]|FI\d{8}|FR[A-Z0-9]{2}\d{9}|HU\d{8}|"
    r"IE\d{7}[A-Z]{1,2}|IT\d{11}|LT(?:\d{9}|\d{12})|LU\d{8}|LV\d{11}|MT\d{8}|NL\d{9}B\d{2}|"
    r"PL\d{10}|PT\d{9}|RO\d{2,10}|SE\d{12}|SI\d{8}|SK\d{10}|XI\d{9}"
    r")\b",
    re.I,
)


def extract_vat(text: str) -> str | None:
    if not text:
        return None
    # Keep token boundaries (don't glue 'VAT' onto the number); also try with
    # internal spaces removed for VATs written with separators.
    up = text.upper()
    m = _VAT_RE.search(up) or _VAT_RE.search(re.sub(r"(?<=[A-Z0-9])\s+(?=[A-Z0-9])", "", up))
    return m.group(1) if m else None


def to_row(it: dict) -> dict | None:
    if not it.get("valid"):
        return None
    cc = it.get("countryCode")
    cc = "GR" if cc == "EL" else cc  # VIES uses EL for Greece
    return {
        "number": it.get("vatNumber"),
        "name": it.get("name") or it.get("traderName"),
        "legal_form": it.get("traderCompanyType"),
        "city": it.get("traderCity"),
        "status": None,
        "incorporation_date": None,
        "country": cc,
        "court": None,
        "address": it.get("address"),
        "url": None,
        "snippet": " · ".join(
            str(x)
            for x in ("VAT valid",
                      f"reliability {it['countryReliability']}" if it.get("countryReliability") else None,
                      f"risk {it['riskLevel']}" if it.get("riskLevel") else None)
            if x
        ),
        "metadata": {
            "vat_valid": True,
            "risk_level": it.get("riskLevel"),
            "risk_score": it.get("riskScore"),
        },
    }


async def search_companies(query: str, token: str, *, limit: int = 10, timeout: float = 60.0) -> list[dict]:
    vat = extract_vat(query)
    if not vat:
        return []
    items = await run_actor_get_items(ACTOR, {"vatNumbers": [vat], "mode": "auto"}, token, timeout=timeout)
    rows = [to_row(it) for it in items]
    return [r for r in rows if r][:limit]
