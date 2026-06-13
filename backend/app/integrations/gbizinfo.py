"""Client for Japan's gBizINFO (METI) company information API.

Free but token-gated: register at https://info.gbiz.go.jp/api/ for an API token.
The provider self-disables when no token is configured (same convention as
Companies House / KVK / Handelsregister).

Endpoint: GET https://info.gbiz.go.jp/hojin/v1/hojin?name=<name>
Header:   X-hojinInfo-api-token: <token>
Returns the matching corporations (keyed by the 13-digit Houjin Bangou).
"""

import httpx

API = "https://info.gbiz.go.jp/hojin/v1/hojin"
_UA = "team-simplex-hackathon/1.0 (company search demo)"

# gBizINFO "kind" (法人種別) code -> readable legal form; unknown codes pass through.
_KIND = {
    "101": "国の機関",
    "201": "地方公共団体",
    "301": "株式会社",
    "302": "有限会社",
    "303": "合名会社",
    "304": "合資会社",
    "305": "合同会社",
    "399": "その他の設立登記法人",
    "401": "外国会社等",
    "499": "その他",
}


def _to_row(h: dict) -> dict:
    """Map a gBizINFO hojin entry to the shared register-row shape."""
    num = h.get("corporate_number")
    return {
        "number": num,
        "name": h.get("name"),
        "legal_form": _KIND.get(str(h.get("kind") or ""), h.get("kind")),
        "city": h.get("location"),
        "status": "closed" if h.get("close_date") else None,
        "incorporation_date": h.get("date_of_establishment"),
        "country": "JP",
        "address": h.get("location"),
        "url": f"https://info.gbiz.go.jp/hojin/ichiran?hojinBango={num}" if num else None,
    }


async def search_companies(name: str, token: str, limit: int = 10) -> list[dict]:
    """Search Japanese corporations by name via gBizINFO (token required)."""
    headers = {"User-Agent": _UA, "Accept": "application/json", "X-hojinInfo-api-token": token}
    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        resp = await client.get(API, params={"name": name, "limit": max(1, min(limit, 50))})
        resp.raise_for_status()
        data = resp.json()

    # The list key is hyphenated in the API ("hojin-infos"); tolerate either form.
    infos = data.get("hojin-infos") or data.get("hojin_infos") or []
    return [_to_row(h) for h in infos[:limit] if h.get("name")]
