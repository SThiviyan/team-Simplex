"""Layer 2 — enrichment of an already-identified entity (Tier A + B fields).

Layer 1 answers "which legal entity is this"; Layer 2 fills registered_address,
incorporation_date, organization_type, status and officers for that entity.
Three passes, cheapest first, each only touching fields still empty:

1. Deterministic reuse: the gather layer's records already carry Tier A data
   (registries, GLEIF, Wikidata) — merge fields from records that match the
   winning entity, ordered by source authority (national register > GLEIF >
   Wikidata). Zero extra calls.
2. Cross-reference lookups: a matching record's LEI -> GLEIF detail record;
   its QID -> Wikidata claims (inception, legal form, HQ, officers). Keyless,
   deterministic, one call each.
3. One bounded web-search call (fast tier) for fields still missing — only
   when the identity is confident; unknown stays null. Wrong enrichment is
   penalized, blank is neutral (PDF scoring), so every pass prefers empty
   over guessed.

This module also owns the row's calibration verdict (confidence_flag +
confidence caps) and echoes the jurisdiction in the caller's convention
(UK question -> UK answer, GB -> GB).
"""

import asyncio
import logging
import re
from functools import cache

import anthropic

from app.config import settings
from app.pipeline import event_log
from app.pipeline.confidence import FLAG_AMBIGUOUS, compute_confidence
from app.pipeline.models import EnrichmentPayload, ExtractionResult, QueryRow
from app.search.base import normalize_country
from app.search.source_ranking import order_records

logger = logging.getLogger(__name__)

ENRICHABLE_FIELDS = (
    "registry_court",
    "registered_address",
    "incorporation_date",
    "organization_type",
    "status",
    "vat_number",
    "trade_names",
    "industry_code",
    "industry",
    "capitalization",
    "business_purpose",
    "officers",
)

# confidence_flag vocabulary + the calibration scorer live in confidence.py.

# Canonical status vocabulary + per-language register terms (static domain
# vocabulary, not data-dependent tuning).
_STATUS_CANONICAL = {
    "active": ("active", "aktiv", "aktuell", "normal", "registered", "bestehend", "vigente"),
    "dissolved": (
        "dissolved", "deleted", "removed", "gelöscht", "aufgelöst", "slettet",
        "ophørt", "ceased", "closed", "inactive", "terminated", "struck",
    ),
    "in_liquidation": ("liquidation", "liquidations", "abwicklung", "winding"),
    "dormant": ("dormant", "ruhend"),
}

_DATE_PATTERNS = (
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})"),                 # ISO (possibly datetime)
    re.compile(r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})$"),   # 31.12.1999
    re.compile(r"^(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})$"),     # 31/12/1999
    re.compile(r"^(?P<y>\d{4})$"),                            # year only
)


def names_agree(a: str | None, b: str | None) -> bool:
    """Loose same-entity check: normalized containment either way."""
    na = " ".join((a or "").lower().split())
    nb = " ".join((b or "").lower().split())
    return bool(na) and bool(nb) and (na in nb or nb in na)


def normalize_status(raw: str | None) -> str | None:
    """Map a source's status wording onto the canonical vocabulary; unknown
    wordings pass through lowercased rather than being guessed."""
    if not raw or not raw.strip():
        return None
    lowered = raw.strip().lower()
    for canonical, needles in _STATUS_CANONICAL.items():
        if any(needle in lowered for needle in needles):
            return canonical
    return lowered


def normalize_date(raw: str | None) -> str | None:
    """ISO-format a date when the pattern is unambiguous; else pass through."""
    if not raw or not str(raw).strip():
        return None
    value = str(raw).strip()
    for pattern in _DATE_PATTERNS:
        m = pattern.match(value)
        if not m:
            continue
        groups = m.groupdict()
        if groups.get("y"):
            if groups.get("d"):
                return f"{int(groups['y']):04d}-{int(groups['m']):02d}-{int(groups['d']):02d}"
            return groups["y"]
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return value


_ALNUM = re.compile(r"[^a-z0-9]")


def _ids_match(a: str | None, b: str | None) -> bool:
    na = _ALNUM.sub("", (a or "").lower())
    nb = _ALNUM.sub("", (b or "").lower())
    return bool(na) and bool(nb) and (na == nb or na.lstrip("0") == nb.lstrip("0"))


@cache
def _registry_provider_names() -> frozenset[str]:
    """Names of the jurisdiction-scoped national registers (derived from the
    provider catalogue, never hardcoded here)."""
    from app.search.sources import all_providers

    return frozenset(p.name for p in all_providers() if p.jurisdictions is not None)


def matching_records(result: ExtractionResult, records: list[dict]) -> list[dict]:
    """Gathered records that are evidence about the WINNING entity, ordered by
    the country's source hierarchy (foundation/registry first, Wikidata last).
    Matching by registry number or registered name keeps other candidates' data
    from leaking into the wrong row."""
    matched = [
        r
        for r in records
        if _ids_match(result.registry_id, r.get("registry_id"))
        or names_agree(result.name_normalized_register_name, r.get("name_normalized_register_name"))
    ]
    # Rank by the confirmed country; if the row hasn't confirmed one yet, infer
    # it from the matched records (a handelsregister record is DE, etc.).
    country = result.jurisdiction_confirmed or next(
        (r.get("jurisdiction_confirmed") for r in matched if r.get("jurisdiction_confirmed")), None
    )
    return order_records(matched, country)


def most_recent_date(*candidates: str | None) -> str | None:
    """The LATEST of the candidate ISO dates. A brand is founded before its
    current legal entity is incorporated (Tesco: 1919 founding vs 1947
    Companies House incorporation) — when sources disagree, the most recent
    date is the registration of the entity itself. ISO strings compare
    lexicographically; a bare year sorts before that year's full dates."""
    dates = [normalize_date(c) for c in candidates]
    dates = [d for d in dates if d and re.match(r"^\d{4}(-\d{2}-\d{2})?$", d)]
    return max(dates) if dates else None


# --- categorical-field normalizers (for conflict detection) -----------------

_LEGAL_FORM_SYNONYMS = {
    "gmbh": "gmbh", "gesellschaft mit beschrankter haftung": "gmbh",
    "ag": "ag", "aktiengesellschaft": "ag",
    "ug": "ug", "unternehmergesellschaft": "ug", "unternehmergesellschaft haftungsbeschrankt": "ug",
    "ek": "ek", "eingetragener kaufmann": "ek",
    "ltd": "ltd", "limited": "ltd", "private company limited by shares": "ltd",
    "ltd.": "ltd",
    "plc": "plc", "public limited company": "plc",
    "bv": "bv", "b.v.": "bv", "besloten vennootschap": "bv",
    "sarl": "sarl", "s.a r.l.": "sarl", "s.a.r.l.": "sarl",
    "sa": "sa", "s.a.": "sa", "societe anonyme": "sa",
    "srl": "srl", "s.r.l.": "srl",
    "sas": "sas",
}


def _legal_form_key(value: str | None) -> str | None:
    if not value:
        return None
    k = re.sub(r"[^a-z0-9 ]", "", _ascii_fold(value).lower()).strip()
    return _LEGAL_FORM_SYNONYMS.get(k, k) or None


def _ascii_fold(value: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def _text_key(value: str | None) -> str | None:
    """Loose text fingerprint for conflict detection: ascii-folded, lowercased,
    whitespace-collapsed. Formatting-only differences don't read as conflicts."""
    if not value:
        return None
    return " ".join(_ascii_fold(value).lower().split()) or None


def resolve_conflict(values: list[tuple[str, str | None]]) -> str | None:
    """Apply the source-hierarchy conflict rule to a rank-ordered list of
    (raw_value, data_timestamp) for one field.

    - All non-null values agree (after normalization, done by the caller) ->
      the top-ranked raw value wins (hierarchy fills top-down).
    - They disagree -> the field is LEFT BLANK, UNLESS the conflicting values
      both carry a data timestamp, in which case the newest wins.
    """
    present = [(raw, ts) for raw, ts in values if raw]
    if not present:
        return None
    raws = {raw for raw, _ in present}
    if len(raws) == 1:
        return present[0][0]  # unanimous -> top-ranked (caller passed in order)
    timestamped = [(raw, ts) for raw, ts in present if ts]
    if len({raw for raw, _ in timestamped}) > 1:
        return max(timestamped, key=lambda x: x[1])[0]  # conflicting + dated -> newest
    return None  # genuine conflict, no timestamps to break it -> blank


def _merge_from_records(
    result: ExtractionResult, matched: list[dict]
) -> tuple[ExtractionResult, list[dict]]:
    """Fill the table top-down by the country's source hierarchy and report any
    contradictions found along the way.

    `matched` is already ordered foundation-first. For each field we take the
    highest-ranked source's value; on a genuine cross-source CONFLICT the field
    is left blank unless data timestamps let the newer value win. Each such
    unresolved disagreement is recorded as a contradiction (field + the
    conflicting values and the sources that gave them) so the caller can surface
    it and lower confidence. incorporation_date keeps the most-recent-date rule.
    Fields already set on `result` (from the Layer-1 agent) are preserved.
    """
    updates: dict = {}
    contradictions: list[dict] = []

    # Categorical fields: resolve by hierarchy + conflict rule on normalized
    # values. `canonical` fields (status) store the normalized value; the rest
    # keep the source's raw form. Tuple: (field, record key, normalizer, canonical).
    field_specs = (
        ("organization_type", "organization_type", _legal_form_key, False),
        ("status", "status", normalize_status, True),
        ("registered_address", "address", _address_key, False),
        ("registry_court", "registry_court", _text_key, False),
        ("vat_number", "vat_number", lambda v: _ALNUM.sub("", (v or "").lower()) or None, False),
        ("trade_names", "trade_names", _text_key, False),
        ("industry_code", "industry_code", lambda v: _ALNUM.sub("", (v or "").lower()) or None, False),
        ("industry", "industry", _text_key, False),
        ("capitalization", "capitalization", _text_key, False),
        ("business_purpose", "business_purpose", _text_key, False),
        ("officers", "officers", _text_key, False),
    )
    for field, key, normalize, canonical in field_specs:
        # Group raw values by normalized key, keeping rank order; collapse
        # format-only differences so they don't read as conflicts.
        by_norm: dict[str, tuple[str, str | None, str | None]] = {}
        for r in matched:
            raw = r.get(key)
            norm = normalize(raw)
            if norm and norm not in by_norm:
                by_norm[norm] = (norm if canonical else raw, r.get("last_update"), r.get("provider"))
        if not by_norm:
            continue  # no source data for this field — keep the agent's value
        resolved = resolve_conflict([(v, ts) for v, ts, _ in by_norm.values()])
        if resolved:
            # Hierarchy (or newest on a dated conflict) fills/overrides.
            updates[field] = resolved
        elif len(by_norm) >= 2:
            # Sources genuinely disagree and nothing breaks the tie -> blank the
            # field AND record the contradiction.
            updates[field] = None
            contradictions.append({
                "field": field,
                "values": [{"value": v, "source": src} for v, _, src in by_norm.values()],
            })

    # incorporation_date: most-recent date across all matched records + any
    # value the agent already had (deterministic conflict resolution — the
    # entity's incorporation is later than the brand's founding — so this field
    # is never blanked on date disagreement).
    date = most_recent_date(
        result.incorporation_date, *[r.get("incorporation_date") for r in matched]
    )
    if date and date != result.incorporation_date:
        updates["incorporation_date"] = date

    merged = result.model_copy(update=updates) if updates else result
    return merged, contradictions


def _address_key(value: str | None) -> str | None:
    """Loose address fingerprint: the set of alphanumeric tokens >= 2 chars, so
    formatting-only differences ('Hamilton, BM' vs 'Hamilton BM') agree while
    genuinely different addresses conflict."""
    if not value:
        return None
    tokens = re.findall(r"[a-z0-9]{2,}", _ascii_fold(value).lower())
    return " ".join(sorted(set(tokens))) or None


def _adopt_foundation_registry_id(
    result: ExtractionResult, matched: list[dict]
) -> ExtractionResult:
    """When the row has no registry_id, take it from the highest-ranked
    FOUNDATION record that has one (matched records are foundation-first). The
    matched set already agrees on the entity by name/id, so this just moves the
    grounded number from e.g. GLEIF onto a row the agent anchored on Wikidata."""
    from app.search.source_ranking import is_foundation_source

    if result.registry_id:
        return result
    for r in matched:
        rid = r.get("registry_id")
        if rid and is_foundation_source(r.get("provider")):
            return result.model_copy(
                update={
                    "registry_id": rid,
                    "registry_court": result.registry_court or r.get("registry_court"),
                    "name_normalized_register_name": (
                        result.name_normalized_register_name
                        or r.get("name_normalized_register_name")
                    ),
                    "jurisdiction_confirmed": (
                        result.jurisdiction_confirmed or r.get("jurisdiction_confirmed")
                    ),
                    "source": result.source or r.get("source"),
                    "no_match_reason": None,
                }
            )
    return result


async def _gleif_name_backfill(
    result: ExtractionResult, query: QueryRow, run_id: str
) -> ExtractionResult:
    """One GLEIF search by the identified FULL name to recover a registry number
    when the row has none. Adopts a registeredAs from a GLEIF record whose name
    agrees and whose country matches — grounded, single bounded call."""
    from app.integrations import gleif
    from app.search.base import normalize_country

    name = result.name_normalized_register_name
    if not name:
        return result
    want = normalize_country((result.jurisdiction_confirmed or query.jurisdiction or "").split("-")[0])
    entities = await gleif.search_entities(name, limit=5)
    for e in entities:
        rid = e.get("registered_as")
        if not rid or not names_agree(name, e.get("name")):
            continue
        ej = (e.get("jurisdiction") or e.get("country") or "").upper()
        if want and ej and normalize_country(ej.split("-")[0]) != want:
            continue
        await event_log.log_event(
            run_id, "gleif_backfill", query.query_id, registry_id=rid, name=e.get("name")
        )
        return result.model_copy(
            update={
                "registry_id": rid,
                "name_normalized_register_name": e.get("name") or name,
                "jurisdiction_confirmed": result.jurisdiction_confirmed or ej or None,
                "registered_address": result.registered_address or e.get("address"),
                "organization_type": result.organization_type or e.get("organization_type"),
                "status": result.status or e.get("entity_status"),
                "source": e.get("record_url") or result.source,
                "no_match_reason": None,
            }
        )
    return result


def _missing_fields(result: ExtractionResult) -> list[str]:
    return [f for f in ENRICHABLE_FIELDS if not getattr(result, f)]


# Strings that are JSON fragments / null markers, not real values. A web-search
# payload occasionally leaks one of these into a field; they must never reach
# the CSV. Matches ": null,", ":null", "null", "none", "n/a", "{...}" fragments.
_JUNK_VALUE = re.compile(r"^[\s:,{}\[\]\"']*(null|none|n/?a)[\s:,{}\[\]\"']*$", re.IGNORECASE)
_ALL_DATA_FIELDS = (
    "registry_court",
    "registered_address",
    "incorporation_date",
    "organization_type",
    "status",
    "vat_number",
    "trade_names",
    "industry_code",
    "industry",
    "capitalization",
    "business_purpose",
    "officers",
)


def _scrub(result: ExtractionResult) -> ExtractionResult:
    """Final safety net before output. Three guarantees:

    1. No junk values: any field whose value is a JSON fragment / null-marker
       (e.g. the literal ': null,') becomes None.
    2. AMBIGUOUS -> NOTHING: if two or more entities could be the match, we do
       NOT keep one candidate's attributes. The whole row is blanked except its
       no_match_reason/flag — better empty than a field copied from the wrong
       one of several candidates.
    3. Output contract: a TRUE no-match — no registry_id AND no identified name
       — carries NO Tier A/B data. (A row identified by name but missing only
       the number keeps its enrichment.)
    """
    updates: dict = {}
    str_fields = ("registry_id", "name_normalized_register_name", "source", *_ALL_DATA_FIELDS)
    for field in str_fields:
        value = getattr(result, field)
        if isinstance(value, str) and (not value.strip() or _JUNK_VALUE.match(value.strip())):
            updates[field] = None
    scrubbed = result.model_copy(update=updates) if updates else result

    ambiguous = scrubbed.confidence_flag == FLAG_AMBIGUOUS or (
        scrubbed.no_match_reason or ""
    ).startswith("ambiguous")
    if ambiguous:
        # No single entity was chosen -> assert nothing. Keep only the verdict.
        return scrubbed.model_copy(
            update={
                "registry_id": None,
                "name_normalized_register_name": None,
                "source": None,
                **{f: None for f in _ALL_DATA_FIELDS},
            }
        )

    if not scrubbed.registry_id and not scrubbed.name_normalized_register_name:
        # Nothing was identified -> the row asserts nothing about any company.
        blank = {f: None for f in _ALL_DATA_FIELDS}
        # Drop a non-URL "source" that only made sense alongside an id; a real
        # citing URL may stay (it can document what was searched).
        if scrubbed.source and not scrubbed.source.startswith(("http://", "https://")):
            blank["source"] = None
        scrubbed = scrubbed.model_copy(update=blank)
    return scrubbed


async def _cross_reference(
    result: ExtractionResult, matched: list[dict]
) -> tuple[ExtractionResult, dict]:
    """Pass 2: follow LEI/QID cross-references the gather layer surfaced.

    Also returns the raw Wikidata details so pass 2.5 (Impressum) can reuse
    the official website / Wikipedia article without a second lookup."""
    qid = next((r["metadata"].get("qid") for r in matched if r.get("metadata", {}).get("qid")), None)
    lei = next((r["metadata"].get("lei") for r in matched if r.get("metadata", {}).get("lei")), None)

    # Wikidata (qid) and GLEIF (lei) are independent lookups that read only the
    # matched records' metadata — fetch them CONCURRENTLY instead of one after
    # the other. GLEIF only ever fills gaps Wikidata leaves, so it is only worth
    # fetching while fields are still missing (computed on the incoming result;
    # if Wikidata then fills everything, GLEIF's updates below are simply empty).
    want_gleif = bool(lei and _missing_fields(result))

    async def _wikidata_details() -> dict:
        if not qid:
            return {}
        try:
            from app.integrations import wikidata

            return await wikidata.entity_details(qid)
        except Exception:
            return {}

    async def _gleif_entity() -> dict:
        if not want_gleif:
            return {}
        try:
            from app.integrations import gleif

            return await gleif.get_entity(lei)
        except Exception:
            return {}

    details, entity = await asyncio.gather(_wikidata_details(), _gleif_entity())

    # Precedence preserved: apply Wikidata's fields first (it outranks GLEIF for
    # this shared detail set), then let GLEIF fill only what is still missing —
    # identical output to the old sequential version, just overlapped fetches.
    if details:
        updates = {}
        # NOTE: details["incorporation_date"] (Wikidata P571 'inception') is
        # deliberately NOT used — it is the brand's founding (Tesco 1919), not
        # the incorporation of the registered entity (1947). Leaving the field
        # empty lets the web fill find the registry's date with a citation.
        for field, key in (
            ("registered_address", "address"),
            ("organization_type", "organization_type"),
            ("status", "status"),
            ("officers", "officers"),
        ):
            value = details.get(key)
            if value and not getattr(result, field):
                updates[field] = value
        if updates:
            result = result.model_copy(update=updates)

    if entity:
        updates = {}
        for field, key in (
            ("registered_address", "address"),
            ("organization_type", "organization_type"),
        ):
            if entity.get(key) and not getattr(result, field):
                updates[field] = entity[key]
        if entity.get("entity_status") and not result.status:
            updates["status"] = normalize_status(entity["entity_status"])
        if updates:
            result = result.model_copy(update=updates)

    return result, details


async def _impressum_fill(
    result: ExtractionResult,
    query: QueryRow,
    records: list[dict],
    details: dict,
    run_id: str,
) -> tuple[ExtractionResult, dict | None]:
    """Pass 2.5 — the deterministic alternative path: the company's own
    website. DE/AT law mandates register number + court + representatives in
    the Impressum, so one HTTP fetch + regexes yields registry-grade facts
    with no LLM call. Used both to FILL missing fields and to CORROBORATE an
    existing registry_id (an independent agreeing source upgrades the flag).

    Returns the (possibly updated) result plus a synthetic evidence record
    (provider='impressum') when the page agreed on the registry_id."""
    website = details.get("website")
    if not website:
        return result, None
    wants_fill = bool(
        {"registry_court", "officers", "registered_address"} & set(_missing_fields(result))
        or not result.registry_id
    )
    if not wants_fill and not result.registry_id:
        return result, None
    try:
        from app.integrations.impressum import fetch_impressum

        hit = await fetch_impressum(website)
    except Exception:
        logger.exception("impressum fetch failed for %s", query.query_id)
        return result, None
    if hit is None:
        return result, None
    url, facts = hit

    evidence: dict | None = None
    impressum_id = facts.get("registry_id")
    updates: dict = {}
    if impressum_id and result.registry_id and _ids_match(result.registry_id, impressum_id):
        # Independent corroboration of the ID by the company's own site.
        evidence = {
            "registry_id": impressum_id,
            "name_normalized_register_name": result.name_normalized_register_name,
            "provider": "impressum",
            "source": url,
            "metadata": {},
        }
    elif impressum_id and not result.registry_id:
        # Identity came from the website Wikidata links for this entity; the
        # Impressum is legally required to state THIS company's register entry.
        updates["registry_id"] = impressum_id
        updates["no_match_reason"] = None
        evidence = {
            "registry_id": impressum_id,
            "name_normalized_register_name": result.name_normalized_register_name,
            "provider": "impressum",
            "source": url,
            "metadata": {},
        }
    for field, key in (
        ("registry_court", "registry_court"),
        ("officers", "officers"),
        ("registered_address", "registered_address"),
        ("vat_number", "vat_number"),  # §5 Impressum must print the USt-IdNr
    ):
        if facts.get(key) and not getattr(result, field):
            updates[field] = facts[key]
    if updates:
        if not result.source:
            updates.setdefault("source", url)
        result = result.model_copy(update=updates)
    if evidence is not None:
        records.append(evidence)
    await event_log.log_event(
        run_id, "impressum_checked", query.query_id,
        url=url, facts=sorted(facts.keys()),
        corroborates=bool(evidence), filled=sorted(set(updates) - {"no_match_reason", "source"}),
    )
    return result, evidence


_WEB_SYSTEM = """You are an enrichment agent in a KYB pipeline. The legal entity is ALREADY
identified — do not re-identify it. Fill ONLY the requested missing fields for exactly
this entity, using web search.

Rules:
- Only state a value you can support with a source found in this search — prefer the
  official register, then the company's own site, then reputable data aggregators.
- A wrong value is penalized; null is neutral. When sources conflict or you are not
  sure the page describes THIS entity (same registry number / same registered name and
  place), return null for that field.
- registered_address: the registered/legal address, as 'street, postcode, city, country'.
- incorporation_date: the REGISTRATION/incorporation date with the register, NOT the founding/establishment year. ISO YYYY-MM-DD (YYYY alone if only the year is verifiable).
- organization_type: the legal form as registered (GmbH, Ltd, B.V., S.à r.l., ...).
- status: one of active / dissolved / in_liquidation / dormant, else null.
- vat_number: the VAT / USt-IdNr / TVA number exactly as printed (e.g. 'DE266929333').
- trade_names: trading/brand names distinct from the legal name, 'name; name'.
- industry_code: the industry classification code (NACE/NAF/SIC/WZ, e.g. '62.01').
- industry: the industry/sector name for that code.
- capitalization: registered/share capital with currency (e.g. 'EUR 25,000').
- business_purpose: the registered business purpose/object of the company.
- officers: 'role: name; role: name' for directors/officers a source explicitly lists.
- source: ONE URL of the page that supports the filled values; null if nothing filled.
- Respond with the JSON object only."""

_WEB_OUTPUT_FORMAT = {
    "type": "json_schema",
    "schema": EnrichmentPayload.model_json_schema(),
}

MAX_WEB_CONTINUATIONS = 4


async def _web_fill(
    result: ExtractionResult, query: QueryRow, run_id: str
) -> ExtractionResult:
    """Pass 3: one bounded web-search call for fields no connected source had."""
    missing = _missing_fields(result)
    if not missing:
        return result

    identity = "\n".join(
        f"{label}: {value}"
        for label, value in (
            ("Registered name", result.name_normalized_register_name),
            ("Registry number", result.registry_id),
            ("Registry court/office", result.registry_court),
            ("Jurisdiction", result.jurisdiction_confirmed or query.jurisdiction),
            ("Search query it was found for", query.name),
        )
        if value
    )
    prompt = (
        f"Entity:\n{identity}\n\nMissing fields to fill (everything else is already "
        f"known — return null for fields not in this list): {', '.join(missing)}"
    )

    client = anthropic.AsyncAnthropic(max_retries=4)
    messages = [{"role": "user", "content": prompt}]
    response = None
    for _ in range(MAX_WEB_CONTINUATIONS):
        response = await client.messages.create(
            model=settings.enrichment_model,
            max_tokens=4000,
            system=[{"type": "text", "text": _WEB_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search"}],
            output_config={"format": _WEB_OUTPUT_FORMAT},
        )
        if response.stop_reason == "pause_turn":
            messages = messages[:1] + [{"role": "assistant", "content": response.content}]
            continue
        break
    if response is None or response.stop_reason == "refusal":
        return result
    try:
        text = next(b.text for b in reversed(response.content) if b.type == "text")
        payload = EnrichmentPayload.model_validate_json(text)
    except (StopIteration, ValueError):
        return result

    updates: dict = {}
    for field in missing:
        value = getattr(payload, field)
        if not value:
            continue
        if field == "status":
            value = normalize_status(value)
            if value not in _STATUS_CANONICAL:
                continue  # the model must commit to the canonical vocabulary
        if field == "incorporation_date":
            value = normalize_date(value)
        updates[field] = value
    if updates and payload.source and not result.source:
        updates["source"] = payload.source
    if updates:
        await event_log.log_event(
            run_id, "enrichment_web_fill", query.query_id,
            fields=sorted(updates.keys() - {"source"}), source=payload.source,
        )
        result = result.model_copy(update=updates)
    return result


def _echo_jurisdiction(result: ExtractionResult, query: QueryRow) -> str | None:
    """Answer in the caller's convention: confirming 'UK' as 'GB' is the same
    fact spelled differently — echo the query's code when they normalize to
    the same place (incl. state codes)."""
    confirmed = (result.jurisdiction_confirmed or "").strip()
    asked = (query.jurisdiction or "").strip()
    if not confirmed:
        return result.jurisdiction_confirmed

    def norm(code: str) -> str:
        parts = code.upper().split("-")
        parts[0] = normalize_country(parts[0]) or parts[0]
        return "-".join(parts)

    if norm(confirmed) == norm(asked):
        return asked
    if norm(confirmed.split("-")[0]) == norm(asked.split("-")[0]) and "-" in confirmed:
        # Same country, ours adds a region: keep the region, spell the country
        # the way the caller did (UK-… for a UK question).
        return f"{asked.split('-')[0]}-{confirmed.split('-', 1)[1]}"
    return result.jurisdiction_confirmed


async def enrich_result(
    query: QueryRow, result: ExtractionResult, records: list[dict], run_id: str
) -> ExtractionResult:
    """Run Layer 2 on a finished Layer-1 row; always returns a flagged row."""
    matched = matching_records(result, records)

    has_identity = bool(result.registry_id or result.name_normalized_register_name)
    contradictions: list[dict] = []
    if has_identity and not settings.pipeline_mock:
        # Foundation supplies the registry_id: if the agent identified the entity
        # by name but anchored on a non-registry source (e.g. Wikidata), adopt the
        # official number from the top-ranked FOUNDATION record for the same
        # entity (e.g. GLEIF's registeredAs). It's grounded (came from a tool
        # result) and the hierarchy already vetted that the names agree.
        result = _adopt_foundation_registry_id(result, matched)
        result, contradictions = _merge_from_records(result, matched)
        details: dict = {}
        try:
            result, details = await _cross_reference(result, matched)
        except Exception:
            logger.exception("cross-reference enrichment failed for %s", query.query_id)
        # Pass 2.5 — the company's own Impressum: deterministic, fast, and an
        # INDEPENDENT corroborator of the registry_id (can upgrade the flag).
        try:
            result, evidence = await _impressum_fill(result, query, records, details, run_id)
            if evidence is not None:
                matched.append(evidence)
        except Exception:
            logger.exception("impressum enrichment failed for %s", query.query_id)
        # Registry backfill: an entity identified by name (e.g. via SEC/Wikidata)
        # but with no official number — re-query GLEIF by the FULL registered
        # name to grab its registeredAs (GLEIF's search misses the US flagship on
        # a one-word query but finds it on the full name, e.g. Palantir).
        if not result.registry_id and result.confidence >= 0.7:
            try:
                result = await _gleif_name_backfill(result, query, run_id)
            except Exception:
                logger.exception("gleif name backfill failed for %s", query.query_id)

        # Web fill only for confidently identified entities: enriching the
        # wrong company is worse than returning blanks. Opt-out for fast batches
        # (it's the main per-row LLM latency cost) — like MCP's owner-lookup flag.
        if settings.enrichment_web_fill and result.confidence >= 0.7 and _missing_fields(result):
            try:
                result = await _web_fill(result, query, run_id)
            except Exception:
                logger.exception("web enrichment failed for %s", query.query_id)
        if result.status:
            result = result.model_copy(update={"status": normalize_status(result.status)})
        if result.incorporation_date:
            result = result.model_copy(
                update={"incorporation_date": normalize_date(result.incorporation_date)}
            )

    # Contradictions are reasons for doubt: surface each one. The calibration
    # scorer (below) turns them into a confidence penalty + flag downgrade.
    for c in contradictions:
        await event_log.log_event(
            run_id, "contradiction", query.query_id,
            field=c["field"], values=c["values"],
        )

    # ONE deterministic calibration verdict: the flag and the [0, 1] number are
    # both derived from the same observable evidence signals (registry-backing,
    # corroboration, jurisdiction alignment, Tier A coverage, contradictions),
    # so two equally-evidenced rows always score the same and every point is
    # attributable. See app/pipeline/confidence.py.
    scored = compute_confidence(result, matched, contradictions, query)

    # Output formatting LAST (after grounding/matching ran on raw values):
    # reshape registry_id/court to the jurisdiction's conventional form
    # ("56247t" -> "FN 56247 t", "Local Court Munich" -> "Amtsgericht München").
    from app.search.registry_format import normalize_registry_court, normalize_registry_id

    cc = (result.jurisdiction_confirmed or query.jurisdiction or "").split("-")[0]
    result = result.model_copy(
        update={
            "registry_id": normalize_registry_id(cc, result.registry_id),
            "registry_court": normalize_registry_court(cc, result.registry_court),
            "confidence_flag": scored.flag,
            "confidence": scored.value,
            "jurisdiction_confirmed": _echo_jurisdiction(result, query),
        }
    )
    # Scrub junk + enforce the no-id-means-no-data contract on the final row.
    result = _scrub(result)
    await event_log.log_event(
        run_id, "enrichment_done", query.query_id,
        flag=scored.flag, confidence=result.confidence,
        signals=scored.as_event(),
        filled=[f for f in ENRICHABLE_FIELDS if getattr(result, f)],
        empty=_missing_fields(result),
        contradictions=len(contradictions),
    )
    return result
