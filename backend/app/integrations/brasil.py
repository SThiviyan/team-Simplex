"""Client for Brazil's company registry (Receita Federal CNPJ) via BrasilAPI.

Free, keyless. BrasilAPI exposes the official CNPJ data by registration number:
``GET https://brasilapi.com.br/api/cnpj/v1/{cnpj}``. There is no public name
search, so this client resolves a company when the query CONTAINS a CNPJ (KYC
inputs frequently carry the registration number); a pure-name query yields
nothing here and is left to GLEIF / the scrape layer.

Docs: https://brasilapi.com.br/docs
"""

import re

import httpx

API = "https://brasilapi.com.br/api/cnpj/v1"
_UA = "team-simplex-hackathon/1.0 (company search demo)"
# A formatted CNPJ (12.345.678/0001-95) or a bare 14-digit run.
_CNPJ_RE = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}")


def extract_cnpj(text: str) -> str | None:
    """Return the 14-digit CNPJ (digits only) found in ``text``, or None."""
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 14:
        return digits
    m = _CNPJ_RE.search(text)
    if m:
        d = re.sub(r"\D", "", m.group(0))
        if len(d) == 14:
            return d
    return None


def _to_row(d: dict) -> dict:
    """Map a BrasilAPI CNPJ response to the shared register-row shape."""
    cnpj = re.sub(r"\D", "", str(d.get("cnpj") or ""))
    street = " ".join(
        str(p)
        for p in (d.get("descricao_tipo_de_logradouro"), d.get("logradouro"), d.get("numero"))
        if p
    )
    address = ", ".join(
        str(x) for x in (street, d.get("bairro"), d.get("municipio"), d.get("uf"), d.get("cep")) if x
    )
    return {
        "number": cnpj or None,
        "name": d.get("razao_social") or d.get("nome_fantasia"),
        "legal_form": d.get("natureza_juridica"),
        "city": d.get("municipio"),
        "status": d.get("descricao_situacao_cadastral"),
        "incorporation_date": d.get("data_inicio_atividade"),
        "country": "BR",
        "address": address or None,
        "url": f"{API}/{cnpj}" if cnpj else None,
    }


async def search_companies(query: str, limit: int = 10) -> list[dict]:
    """Resolve a Brazilian company by the CNPJ embedded in the query (if any)."""
    cnpj = extract_cnpj(query)
    if not cnpj:
        return []  # no public name search — covered by GLEIF / the scrape layer
    async with httpx.AsyncClient(
        timeout=20.0, headers={"User-Agent": _UA, "Accept": "application/json"}
    ) as client:
        resp = await client.get(f"{API}/{cnpj}")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()

    row = _to_row(data)
    return [row] if row.get("name") else []
