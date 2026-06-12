"""Client for the Wikidata SPARQL endpoint (query.wikidata.org).

Wikidata is a free, keyless knowledge base. Text search is done through the
`wikibase:mwapi` EntitySearch service inside SPARQL; arbitrary SPARQL is also
supported. Wikimedia REQUIRES a descriptive User-Agent.

Shared by the Wikidata MCP server and the Wikidata search provider.
"""

from app.integrations.http import shared_client

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
ENTITY_WEB = "https://www.wikidata.org/wiki"
# Wikimedia policy: identify the client or requests get 403/blocked.
USER_AGENT = "team-simplex-hackathon/1.0 (federated company search; Sinpex Hackathon 2026)"

_HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": USER_AGENT,
}


def _escape(term: str) -> str:
    """Make a user string safe to embed inside a SPARQL double-quoted literal."""
    return (
        term.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


async def run_sparql(query: str) -> dict:
    """Run an arbitrary SPARQL query and return the raw JSON results object.

    HTTP/2 is required: WDQS's anti-bot WAF rejects HTTP/1.1 requests with 403.
    """
    client = shared_client("wikidata", http2=True, headers=_HEADERS, timeout=30.0)
    resp = await client.get(SPARQL_ENDPOINT, params={"query": query, "format": "json"})
    resp.raise_for_status()
    return resp.json()


# Roots whose subclass trees cover "anything registered as a company": business,
# public company, nonprofit, voluntary association (e.V.), cooperative,
# foundation. We keep only entities whose "instance of" (P31) chains up to one
# of these via subclass-of (P279*) — excluding people, foods, places, the SI
# unit "tesla", and other non-registered things. (Union of these smaller trees
# instead of the single huge "organization" Q43229 tree, which is ~20x slower.)
_ORG_ROOTS = (
    "wd:Q4830453",  # business
    "wd:Q891723",  # public company
    "wd:Q163740",  # nonprofit organization
    "wd:Q48204",  # voluntary association
    "wd:Q4539",  # cooperative
    "wd:Q157031",  # foundation
)


def _entity_search_query(name: str, limit: int) -> str:
    n = min(max(limit, 1), 50)
    # Over-fetch candidates since the org-type filter drops non-companies.
    candidates = min(50, max(n * 5, 10))
    return f"""
SELECT DISTINCT ?item ?itemLabel ?itemDescription ?sitelinks ?cc WHERE {{
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:api "EntitySearch" ;
                    wikibase:endpoint "www.wikidata.org" ;
                    mwapi:search "{_escape(name)}" ;
                    mwapi:language "en" ;
                    mwapi:limit "{candidates}" .
    ?item wikibase:apiOutputItem mwapi:item .
  }}
  ?item wdt:P31/wdt:P279* ?orgClass .
  VALUES ?orgClass {{ {" ".join(_ORG_ROOTS)} }}
  ?item wikibase:sitelinks ?sitelinks .
  OPTIONAL {{ ?item wdt:P17/wdt:P297 ?cc . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul". }}
}}
ORDER BY DESC(?sitelinks)
LIMIT {candidates}
""".strip()


async def search_entities(name: str, limit: int = 10) -> list[dict]:
    """Search Wikidata for registered companies/organisations by name."""
    data = await run_sparql(_entity_search_query(name, limit))
    seen: set[str] = set()
    out: list[dict] = []
    for b in data.get("results", {}).get("bindings", []):
        uri = b.get("item", {}).get("value", "")
        qid = uri.rsplit("/", 1)[-1] if uri else None
        if qid and qid in seen:  # the country OPTIONAL can duplicate an entity
            continue
        if qid:
            seen.add(qid)
        # The label service echoes the QID when there's no label; treat that as
        # unusable so the provider skips it.
        label = b.get("itemLabel", {}).get("value")
        if label == qid:
            label = None
        try:
            sitelinks = int(b.get("sitelinks", {}).get("value", 0))
        except (TypeError, ValueError):
            sitelinks = 0
        cc = b.get("cc", {}).get("value")
        out.append(
            {
                "qid": qid,
                "label": label,
                "description": b.get("itemDescription", {}).get("value"),
                "url": f"{ENTITY_WEB}/{qid}" if qid else None,
                "sitelinks": sitelinks,
                "jurisdiction": cc.upper() if cc else None,
            }
        )
        if len(out) >= min(max(limit, 1), 50):
            break
    return out


# Properties for the Layer-2 enrichment of an already-identified company.
# P571 inception, P1454 legal form, P159 headquarters location, P6375 street
# address, P576 dissolved/abolished, P169 CEO, P112 founder, P488 chairperson.
_DETAIL_SPARQL = """
SELECT ?inception ?legalFormLabel ?hqLabel ?street ?dissolved ?ceoLabel ?founderLabel ?chairLabel ?website ?article WHERE {{
  OPTIONAL {{ wd:{qid} wdt:P571 ?inception . }}
  OPTIONAL {{ wd:{qid} wdt:P1454 ?legalForm . }}
  OPTIONAL {{ wd:{qid} wdt:P159 ?hq .
             OPTIONAL {{ wd:{qid} p:P159 ?hqStmt . ?hqStmt pq:P6375 ?street . }} }}
  OPTIONAL {{ wd:{qid} wdt:P576 ?dissolved . }}
  OPTIONAL {{ wd:{qid} wdt:P169 ?ceo . }}
  OPTIONAL {{ wd:{qid} wdt:P112 ?founder . }}
  OPTIONAL {{ wd:{qid} wdt:P488 ?chair . }}
  OPTIONAL {{ wd:{qid} wdt:P856 ?website . }}
  OPTIONAL {{ ?article schema:about wd:{qid} ;
                       schema:isPartOf <https://en.wikipedia.org/> . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul". }}
}}
LIMIT 5
"""


def _first(bindings: list[dict], key: str) -> str | None:
    for b in bindings:
        value = (b.get(key) or {}).get("value")
        if value:
            return value
    return None


def _collect(bindings: list[dict], key: str) -> list[str]:
    seen: list[str] = []
    for b in bindings:
        value = (b.get(key) or {}).get("value")
        if value and value not in seen:
            seen.append(value)
    return seen


async def entity_details(qid: str) -> dict:
    """Tier A/B facts for one company item, straight from its Wikidata claims.

    Returns a dict with any of: incorporation_date (ISO date), organization_type,
    address, status ('dissolved' only — Wikidata has no positive 'active' claim),
    officers ('role: name; ...'). Keys with no data are absent.
    """
    qid = qid.strip().upper()
    if not qid.startswith("Q") or not qid[1:].isdigit():
        return {}
    data = await run_sparql(_DETAIL_SPARQL.format(qid=qid))
    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        return {}

    out: dict = {}
    inception = _first(bindings, "inception")
    if inception:
        out["incorporation_date"] = inception[:10]  # xsd:dateTime -> YYYY-MM-DD
    legal_form = _first(bindings, "legalFormLabel")
    if legal_form:
        out["organization_type"] = legal_form
    street, hq = _first(bindings, "street"), _first(bindings, "hqLabel")
    address = ", ".join(p for p in (street, hq if hq != street else None) if p)
    if address:
        out["address"] = address
    if _first(bindings, "dissolved"):
        out["status"] = "dissolved"
    officers = [
        f"{role}: {name}"
        for role, key in (("CEO", "ceoLabel"), ("founder", "founderLabel"), ("chair", "chairLabel"))
        for name in _collect(bindings, key)
    ]
    if officers:
        out["officers"] = "; ".join(officers)
    # Cross-reference targets for the alternative-path loop (Impressum scrape,
    # Wikipedia citation), not output fields themselves.
    website = _first(bindings, "website")
    if website:
        out["website"] = website
    article = _first(bindings, "article")
    if article:
        out["wikipedia"] = article
    return out
