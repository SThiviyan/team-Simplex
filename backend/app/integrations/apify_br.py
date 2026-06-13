"""Brazil — CNPJ (Receita Federal) lookup via the Apify
'jungle_synthesizer/brazil-cnpj-receita-federal-crawler' actor. By CNPJ number.
"""

from app.integrations.apify import run_actor_get_items
from app.integrations.brasil import extract_cnpj

ACTOR = "jungle_synthesizer~brazil-cnpj-receita-federal-crawler"
_SURVEY = {
    "sp_intended_usage": "Company registry KYC search",
    "sp_improvement_suggestions": "n/a",
}


def to_row(it: dict) -> dict:
    street = " ".join(str(x) for x in (it.get("logradouro"), it.get("numero")) if x)
    addr = ", ".join(
        str(x) for x in (street, it.get("bairro"), it.get("municipio"), it.get("uf"), it.get("cep")) if x
    )
    return {
        "number": it.get("cnpj"),
        "name": it.get("razao_social"),
        "legal_form": it.get("natureza_juridica"),
        "city": it.get("municipio"),
        "status": None if (it.get("situacao_cadastral") or "").upper() == "ATIVA" else "dissolved",
        "incorporation_date": it.get("data_abertura"),
        "country": "BR",
        "court": None,
        "address": addr or None,
        "url": None,
        "snippet": " · ".join(
            str(x)
            for x in (it.get("natureza_juridica"), it.get("municipio"), it.get("uf"), it.get("situacao_cadastral"))
            if x
        ),
        "metadata": {
            "nome_fantasia": it.get("nome_fantasia"),
            "porte": it.get("porte"),
            "capital_social": it.get("capital_social"),
            "cnae": it.get("cnae_principal_descricao"),
        },
    }


async def search_companies(query: str, token: str, *, limit: int = 10, timeout: float = 90.0) -> list[dict]:
    cnpj = extract_cnpj(query)
    if not cnpj:
        return []
    items = await run_actor_get_items(
        ACTOR, {**_SURVEY, "cnpj": cnpj, "pageSize": 1}, token, timeout=timeout
    )
    return [to_row(it) for it in items if it.get("razao_social")][:limit]
