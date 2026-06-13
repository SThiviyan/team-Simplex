"""CSV-driven "include everything" search.

The frontend query is treated as CSV with the fields `name,jurisdiction` (one
or more rows). For each row we query every relevant source and include ALL
matching results (no type/relevance filtering). If a jurisdiction is given we
keep only companies in that jurisdiction; otherwise everything is included.

The combined results are also written to a JSON file in the repo so they can be
opened in an IDE.
"""

import asyncio
import csv
import io
import json
from difflib import SequenceMatcher
from pathlib import Path

from app.config import settings
from app.search.base import SearchProvider, SearchResult
from app.search.resolver import _normalise

import logging

logger = logging.getLogger(__name__)

# Lands at backend/search_results.json — inside the bind-mounted repo, so it
# shows up in the IDE.
OUTPUT_FILE = Path(__file__).resolve().parents[2] / "search_results.json"

# Per-source fetch depth so the JSON export captures ALL company results a
# source returns (each provider still clamps to its own API maximum). Full
# pagination isn't implemented, so this is "all" up to each API's single-call cap.
RESULTS_PER_SOURCE = 100


# ISO 3166-1 alpha-2 codes, for recognising a jurisdiction typed without a comma.
_ISO_ALPHA2 = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL "
    "BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV "
    "CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD "
    "GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM "
    "IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK "
    "LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW "
    "MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR "
    "PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS "
    "ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY "
    "UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split()
)
# Codes that double as common company legal-form abbreviations — don't treat a
# trailing one as a jurisdiction (e.g. "BMW AG", "Volvo SE"). Use a comma for these.
_LEGAL_FORM_CODES = frozenset({"AG", "SE", "SA", "AS", "BV", "KG", "SL", "SC"})


def _split_trailing_jurisdiction(name: str, juris: str | None) -> tuple[str, str | None]:
    """Allow "Tesla DE" (no comma) to mean name="Tesla", jurisdiction="DE"."""
    if juris:
        return name, juris
    parts = name.rsplit(None, 1)
    if len(parts) == 2:
        head, tail = parts
        code = tail.upper()
        if code in _ISO_ALPHA2 and code not in _LEGAL_FORM_CODES:
            return head.strip(), code
    return name, juris


def parse_query_csv(text: str) -> list[tuple[str, str | None]]:
    """Parse the query as CSV rows of (name, jurisdiction)."""
    rows: list[tuple[str, str | None]] = []
    for fields in csv.reader(io.StringIO(text.strip())):
        if not fields or not fields[0].strip():
            continue
        name = fields[0].strip()
        juris = fields[1].strip() if len(fields) > 1 and fields[1].strip() else None
        # Skip a header row like "name,jurisdiction".
        if name.lower() == "name" and (juris or "").lower() in ("jurisdiction", "", None):
            continue
        rows.append(_split_trailing_jurisdiction(name, juris))
    return rows


def select_providers(
    providers: list[SearchProvider], jurisdiction: str | None
) -> list[SearchProvider]:
    """With a jurisdiction: global providers + that country's register. Without
    one: every provider (include all)."""
    if not jurisdiction:
        return list(providers)
    cc = jurisdiction.upper()
    return [p for p in providers if p.jurisdictions is None or cc in p.jurisdictions]


async def _search_one(
    provider: SearchProvider, name: str, limit: int
) -> list[SearchResult]:
    """Run one provider under a hard timeout so a slow/hanging source cannot
    stall the whole gather. A timeout (or any error) is non-fatal — empty list."""
    try:
        return await asyncio.wait_for(
            provider.search(name, limit=limit), timeout=settings.provider_timeout
        )
    except asyncio.TimeoutError:
        logger.warning(
            "provider %s exceeded %.0fs timeout; skipping", provider.name, settings.provider_timeout
        )
        return []


async def _gather(
    selected: list[SearchProvider], name: str, jurisdiction: str | None, limit: int
) -> list[SearchResult]:
    batches = await asyncio.gather(
        *(_search_one(p, name, limit) for p in selected), return_exceptions=True
    )
    results: list[SearchResult] = []
    for b in batches:
        if isinstance(b, list):
            results.extend(b)
    if jurisdiction:
        cc = jurisdiction.upper()
        # Keep only companies confirmed to be in that jurisdiction.
        results = [r for r in results if (r.jurisdiction or "").upper() == cc]
    return sorted(results, key=lambda r: r.score, reverse=True)


async def search_jurisdiction(
    providers: list[SearchProvider], name: str, jurisdiction: str | None, limit: int
) -> tuple[list[SearchResult], list[str], list[str]]:
    """Route by jurisdiction, query the relevant sources, filter, and report
    which sources were called vs skipped. Shared by the API and the MCP server.
    """
    selected = select_providers(providers, jurisdiction)
    skipped = [p.name for p in providers if p not in selected]
    results = await _gather(selected, name, jurisdiction, limit)
    return results, [p.name for p in selected], skipped


def _citation(r: SearchResult) -> str:
    """A citable URL or registry document reference supporting the answer."""
    if r.url:
        return r.url
    parts = [p for p in (r.source, r.registry_court, r.registry_id) if p]
    return " — ".join(parts) if parts else r.source


def _to_record(query_id: str, query_name: str, r: SearchResult) -> dict:
    register_name = r.register_name or r.title
    return {
        "query_id": query_id,
        "registry_id": r.registry_id,
        "registry_court": r.registry_court,
        # Full legal name as registered (e.g. "Sinpex GmbH", not "Sinpex").
        "name_normalized_register_name": register_name,
        "jurisdiction_confirmed": r.jurisdiction,
        # How closely the registered name matches the queried name, in [0, 1].
        "confidence": round(
            SequenceMatcher(None, _normalise(query_name), _normalise(register_name)).ratio(), 3
        ),
        "source": _citation(r),
        "no_match_reason": None if r.registry_id else "not_in_registry",
        # Extra entity context carried through to the matching layer / frontend.
        "incorporation_date": r.incorporation_date,
        "organization_type": r.organization_type,
        "status": r.status,
        "last_update": r.last_update,
        "address": r.address,
        # Extra context (display + provenance) — beyond the required schema.
        "provider": r.source,
        "snippet": r.snippet,
    }


def _no_match_record(query_id: str, query_name: str, jurisdiction: str | None) -> dict:
    return {
        "query_id": query_id,
        "registry_id": None,
        "registry_court": None,
        "name_normalized_register_name": None,
        "jurisdiction_confirmed": jurisdiction.upper() if jurisdiction else None,
        "confidence": 0.0,
        "source": None,
        "no_match_reason": "not_in_registry",
        "incorporation_date": None,
        "organization_type": None,
        "status": None,
        "last_update": None,
        "address": None,
        "provider": None,
        "snippet": None,
    }


async def csv_search(providers: list[SearchProvider], csv_text: str, limit: int = 25) -> dict:
    # Fetch generously per source so the export holds every company result
    # found, regardless of the (smaller) display limit a caller might pass.
    fetch_limit = max(limit, RESULTS_PER_SOURCE)
    queries: list[dict] = []
    records: list[dict] = []
    for idx, (name, juris) in enumerate(parse_query_csv(csv_text), start=1):
        query_id = f"q{idx}"
        # A jurisdiction-scoped register is NOT called when the requested
        # jurisdiction can't possibly match it (e.g. the German register is
        # skipped for a Hungarian query).
        results, called, skipped = await search_jurisdiction(providers, name, juris, fetch_limit)
        queries.append(
            {
                "query_id": query_id,
                "name": name,
                "jurisdiction": juris.upper() if juris else None,
                "count": len(results),
                "sources_called": called,
                "sources_skipped": skipped,
            }
        )
        if results:
            records.extend(_to_record(query_id, name, r) for r in results)
        else:
            # No company matched — emit a row explaining why (joins via query_id).
            records.append(_no_match_record(query_id, name, juris))

    payload = {
        "queries": queries,
        "count": len(records),
        "results": records,
    }
    try:
        # The on-disk export omits each record's `confidence` (a cheap name
        # string-similarity). It stays on the in-memory records so the matching
        # layer can still use it as a prior, but it is not written to the file.
        file_results = [{k: v for k, v in rec.items() if k != "confidence"} for rec in records]
        OUTPUT_FILE.write_text(
            json.dumps({**payload, "results": file_results}, indent=2, ensure_ascii=False)
        )
        payload["output_file"] = str(OUTPUT_FILE)
    except OSError as e:  # never fail the request just because the file write did
        payload["output_file_error"] = str(e)
    return payload
