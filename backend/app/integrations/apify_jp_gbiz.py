"""Japan — company data (gBizINFO) NAME search via the Apify
'jungle_synthesizer/gbizinfo-japan-company-scraper' actor. Opt-in.
"""

from app.integrations.apify import run_actor_get_items

ACTOR = "jungle_synthesizer~gbizinfo-japan-company-scraper"
_SURVEY = {
    "sp_intended_usage": "Company registry KYC search",
    "sp_improvement_suggestions": "n/a",
}


def to_row(it: dict) -> dict:
    num = it.get("corporate_number")
    capital = it.get("capital_stock")
    emp = it.get("employee_number")
    return {
        "number": num,
        "name": it.get("name") or it.get("name_en"),
        "legal_form": None,
        "city": None,
        "status": None,
        "incorporation_date": it.get("date_of_establishment"),
        "last_update": it.get("update_date"),
        "country": "JP",
        "court": None,
        "address": it.get("location"),
        "url": it.get("company_url")
        or (f"https://info.gbiz.go.jp/hojin/ichiran?hojinBango={num}" if num else None),
        "snippet": " · ".join(
            str(x)
            for x in (it.get("location"), f"capital {capital}" if capital else None,
                      f"{emp} employees" if emp else None)
            if x
        ),
        "metadata": {
            "representative": it.get("representative_name"),
            "capital_stock": capital,
            "employees": emp,
            "name_en": it.get("name_en"),
        },
    }


async def search_companies(query: str, token: str, *, limit: int = 10, timeout: float = 90.0) -> list[dict]:
    if not query:
        return []
    items = await run_actor_get_items(
        ACTOR, {**_SURVEY, "searchMode": "bySearch", "nameQuery": query}, token, timeout=timeout
    )
    return [to_row(it) for it in items if it.get("name") or it.get("name_en")][:limit]
