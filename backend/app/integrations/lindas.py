"""Client for LINDAS — the Swiss Federal Linked Data portal (lindas.admin.ch).

LINDAS publishes the Zefix commercial register as linked data behind a KEYLESS
public SPARQL endpoint (unlike the Zefix REST API, which needs registration). It
carries the UID (CHE…), legal name, legal form, registered address and the
business purpose — making it a proper foundation source for CH, not just a
supplement. Shared by the LINDAS MCP/search provider.
"""

from app.integrations.http import shared_client

SPARQL_ENDPOINT = "https://lindas.admin.ch/query"
_UA = "team-simplex-hackathon/1.0 (federated company search; Sinpex Hackathon 2026)"
_HEADERS = {"Accept": "application/sparql-results+json", "User-Agent": _UA}


def _escape(term: str) -> str:
    """Make a user string safe inside a SPARQL double-quoted literal."""
    return term.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _format_uid(uid: str | None) -> str | None:
    """'CHE105909036' -> 'CHE-105.909.036' (the conventional Swiss UID form)."""
    if not uid:
        return None
    digits = "".join(c for c in uid if c.isdigit())
    if uid.upper().startswith("CHE") and len(digits) == 9:
        return f"CHE-{digits[0:3]}.{digits[3:6]}.{digits[6:9]}"
    return uid


def _query(name: str, limit: int) -> str:
    return f"""PREFIX schema: <http://schema.org/>
SELECT ?company ?name ?uid ?desc ?lfName ?founding ?street ?plz ?city ?canton WHERE {{
  ?company schema:legalName ?name .
  FILTER(CONTAINS(LCASE(STR(?name)), LCASE("{_escape(name)}")))
  ?company schema:identifier ?uidNode .
  FILTER(CONTAINS(STR(?uidNode), "/UID/CHE"))
  BIND(STRAFTER(STR(?uidNode), "/UID/") AS ?uid)
  OPTIONAL {{ ?company schema:description ?desc }}
  OPTIONAL {{ ?company schema:additionalType ?lf . ?lf schema:name ?lfName . FILTER(LANG(?lfName) = "de") }}
  OPTIONAL {{ ?company schema:foundingDate ?founding }}
  OPTIONAL {{ ?company schema:address ?a .
    OPTIONAL {{ ?a schema:streetAddress ?street }}
    OPTIONAL {{ ?a schema:postalCode ?plz }}
    OPTIONAL {{ ?a schema:addressLocality ?city }}
    OPTIONAL {{ ?a schema:addressRegion ?canton }} }}
}} LIMIT {max(1, min(limit, 20))}"""


def _address(b: dict) -> str | None:
    def v(key: str) -> str | None:
        return (b.get(key) or {}).get("value")

    parts = [v("street"), v("plz"), v("city"), v("canton")]
    joined = ", ".join(p for p in parts if p)
    return f"{joined}, CH" if joined else None


def _normalise(b: dict) -> dict:
    def v(key: str) -> str | None:
        return (b.get(key) or {}).get("value")

    company_uri = v("company")
    return {
        "number": _format_uid(v("uid")),
        "name": v("name"),
        "legal_form": v("lfName"),
        "city": v("city"),
        "address": _address(b),
        "incorporation_date": v("founding"),
        "business_purpose": v("desc"),
        "status": None,  # the register lists active entities; don't guess otherwise
        "country": "CH",
        "court": None,
        "url": company_uri,
    }


async def search_companies(name: str, limit: int = 10) -> list[dict]:
    """Name search of the Swiss commercial register via LINDAS SPARQL. Keyless."""
    client = shared_client("lindas", headers=_HEADERS, timeout=15.0)
    resp = await client.get(SPARQL_ENDPOINT, params={"query": _query(name, limit), "format": "json"})
    resp.raise_for_status()
    rows = resp.json().get("results", {}).get("bindings", [])
    # The same company can appear once per identifier row; de-dup by UID.
    out: list[dict] = []
    seen: set[str] = set()
    for b in rows:
        r = _normalise(b)
        key = r.get("number") or r.get("name") or ""
        if key and key not in seen and r.get("name"):
            seen.add(key)
            out.append(r)
    return out[:limit]
