"""Client for the Czech ARES register (Administrativní registr ekonomických subjektů).

Free, keyless JSON API run by the Czech Ministry of Finance. Searches all
economic subjects (companies, sole traders) by business name.
Docs: https://ares.gov.cz/ekonomicke-subjekty-v-be/rest

Shared by the company-registry MCP server (via the provider) and the
AresSearchProvider so request/normalisation logic lives in one place.
"""

import httpx

API_BASE = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
WEB = "https://ares.gov.cz/ekonomicke-subjekty"
_UA = "team-simplex-hackathon/1.0 (company search demo)"
_HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": _UA}

# pravniForma code -> Czech legal-form name, loaded once from the ARES code list
# (číselník) and cached for the process — e.g. "121" -> "Akciová společnost".
_LEGAL_FORMS: dict[str, str] = {}


async def _legal_forms(client: httpx.AsyncClient) -> dict[str, str]:
    """Lazily load and cache the full právní-forma code list (code -> name)."""
    if _LEGAL_FORMS:
        return _LEGAL_FORMS
    try:
        resp = await client.post(
            "/ciselniky-nazevniky/vyhledat", json={"kodCiselniku": "PravniForma"}
        )
        if resp.status_code == 200:
            for cis in resp.json().get("ciselniky", []):
                if cis.get("kodCiselniku") != "PravniForma":
                    continue
                for item in cis.get("polozkyCiselniku", []):
                    cs = next(
                        (n.get("nazev") for n in (item.get("nazev") or []) if n.get("kodJazyka") == "cs"),
                        None,
                    )
                    if item.get("kod") and cs:
                        _LEGAL_FORMS[str(item["kod"])] = cs
    except Exception:
        pass  # readable legal form is a nice-to-have, never fatal
    return _LEGAL_FORMS


def _normalise(subject: dict, legal_forms: dict[str, str]) -> dict:
    ico = subject.get("ico")
    sidlo = subject.get("sidlo") or {}
    forma = subject.get("pravniForma")
    return {
        "number": ico,  # IČO — the Czech registration number
        "name": subject.get("obchodniJmeno"),
        "legal_form": legal_forms.get(str(forma)) if forma else None,
        "city": sidlo.get("nazevObce"),
        "address": sidlo.get("textovaAdresa"),
        # datumZaniku set => the subject has ceased to exist.
        "status": "zaniklý" if subject.get("datumZaniku") else "aktivní",
        "incorporation_date": subject.get("datumVzniku"),
        # The registered-office country code (ISO alpha-2) the entry itself states,
        # rather than assuming CZ from the server; ~always "CZ" in practice.
        "country": sidlo.get("kodStatu") or "CZ",
        "court": None,
        "url": f"{WEB}?ico={ico}" if ico else None,
    }


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Search the Czech register by company name (obchodní jméno)."""
    body = {"obchodniJmeno": name, "pocet": max(1, min(limit, 100)), "start": 0}
    async with httpx.AsyncClient(base_url=API_BASE, headers=_HEADERS, timeout=20.0) as client:
        resp = await client.post("/ekonomicke-subjekty/vyhledat", json=body)
        resp.raise_for_status()
        subjects = resp.json().get("ekonomickeSubjekty", [])
        forms = await _legal_forms(client)
        return [_normalise(s, forms) for s in subjects]
