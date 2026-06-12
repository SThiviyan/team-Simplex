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

import logging
import re
from functools import cache

import anthropic

from app.config import settings
from app.pipeline import event_log
from app.pipeline.models import EnrichmentPayload, ExtractionResult, QueryRow
from app.search.base import normalize_country

logger = logging.getLogger(__name__)

ENRICHABLE_FIELDS = (
    "registered_address",
    "incorporation_date",
    "organization_type",
    "status",
    "officers",
)

# confidence_flag vocabulary (one per row, drives the confidence caps below).
FLAG_VERIFIED = "verified"      # registry-backed ID, corroborated
FLAG_PROBABLE = "probable"      # ID present but single-source
FLAG_AMBIGUOUS = "ambiguous"    # several candidates, none chosen
FLAG_NOT_FOUND = "not_found"    # honest blank (no ID in any source)
FLAG_ERROR = "error"            # the pipeline failed, not the registry

_ERROR_PREFIXES = ("layer1_error", "pipeline_error")

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


def _authority(provider: str | None) -> int:
    """Merge priority: national register (0) > GLEIF (1) > everything else (2)."""
    if provider in _registry_provider_names():
        return 0
    if provider == "gleif":
        return 1
    return 2


def matching_records(result: ExtractionResult, records: list[dict]) -> list[dict]:
    """Gathered records that are evidence about the WINNING entity, most
    authoritative first. Matching by registry number or registered name keeps
    other candidates' data from leaking into the wrong row."""
    matched = [
        r
        for r in records
        if _ids_match(result.registry_id, r.get("registry_id"))
        or names_agree(result.name_normalized_register_name, r.get("name_normalized_register_name"))
    ]
    matched.sort(key=lambda r: _authority(r.get("provider")))
    return matched


def _merge_from_records(result: ExtractionResult, matched: list[dict]) -> ExtractionResult:
    """Pass 1: fill empty fields from the gathered evidence, best source first."""
    updates: dict = {}
    for record in matched:
        candidates = {
            "registered_address": record.get("address"),
            "incorporation_date": normalize_date(record.get("incorporation_date")),
            "organization_type": record.get("organization_type"),
            "status": normalize_status(record.get("status")),
        }
        for field, value in candidates.items():
            if value and not getattr(result, field) and field not in updates:
                updates[field] = value
    return result.model_copy(update=updates) if updates else result


def _missing_fields(result: ExtractionResult) -> list[str]:
    return [f for f in ENRICHABLE_FIELDS if not getattr(result, f)]


async def _cross_reference(result: ExtractionResult, matched: list[dict]) -> ExtractionResult:
    """Pass 2: follow LEI/QID cross-references the gather layer surfaced."""
    if not _missing_fields(result):
        return result

    qid = next((r["metadata"].get("qid") for r in matched if r.get("metadata", {}).get("qid")), None)
    if qid:
        try:
            from app.integrations import wikidata

            details = await wikidata.entity_details(qid)
        except Exception:
            details = {}
        updates = {}
        for field, key in (
            ("registered_address", "address"),
            ("incorporation_date", "incorporation_date"),
            ("organization_type", "organization_type"),
            ("status", "status"),
            ("officers", "officers"),
        ):
            value = details.get(key)
            if value and not getattr(result, field):
                updates[field] = normalize_date(value) if field == "incorporation_date" else value
        if updates:
            result = result.model_copy(update=updates)

    if _missing_fields(result):
        lei = next(
            (r["metadata"].get("lei") for r in matched if r.get("metadata", {}).get("lei")), None
        )
        if lei:
            try:
                from app.integrations import gleif

                entity = await gleif.get_entity(lei)
            except Exception:
                entity = {}
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
    return result


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
- incorporation_date: ISO YYYY-MM-DD (YYYY alone if only the year is verifiable).
- organization_type: the legal form as registered (GmbH, Ltd, B.V., S.à r.l., ...).
- status: one of active / dissolved / in_liquidation / dormant, else null.
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
            model=settings.matching_model,
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


def _confidence_flag(result: ExtractionResult, matched: list[dict]) -> str:
    reason = result.no_match_reason or ""
    if reason.startswith(_ERROR_PREFIXES):
        return FLAG_ERROR
    if reason.startswith("ambiguous_candidates"):
        return FLAG_AMBIGUOUS
    if not result.registry_id:
        return FLAG_NOT_FOUND
    # Verified: the ID comes from (or is corroborated by) a national register,
    # or two independent sources agree on it.
    providers_with_id = {
        r.get("provider") for r in matched if _ids_match(result.registry_id, r.get("registry_id"))
    }
    if providers_with_id & _registry_provider_names():
        return FLAG_VERIFIED
    if len(providers_with_id) >= 2:
        return FLAG_VERIFIED
    return FLAG_PROBABLE


# Calibration: the flag bounds how confident the row may claim to be. A wrong
# confident answer scores worse than a blank one, so caps only ever lower.
_CONFIDENCE_CAPS = {
    FLAG_ERROR: 0.0,
    FLAG_AMBIGUOUS: 0.4,
    FLAG_NOT_FOUND: 0.6,
    FLAG_PROBABLE: 0.85,
    FLAG_VERIFIED: 1.0,
}


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
    if has_identity and not settings.pipeline_mock:
        result = _merge_from_records(result, matched)
        try:
            result = await _cross_reference(result, matched)
        except Exception:
            logger.exception("cross-reference enrichment failed for %s", query.query_id)
        # Web fill only for confidently identified entities: enriching the
        # wrong company is worse than returning blanks.
        if result.confidence >= 0.7 and _missing_fields(result):
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

    flag = _confidence_flag(result, matched)
    result = result.model_copy(
        update={
            "confidence_flag": flag,
            "confidence": round(min(result.confidence, _CONFIDENCE_CAPS[flag]), 2),
            "jurisdiction_confirmed": _echo_jurisdiction(result, query),
        }
    )
    await event_log.log_event(
        run_id, "enrichment_done", query.query_id,
        flag=flag, confidence=result.confidence,
        filled=[f for f in ENRICHABLE_FIELDS if getattr(result, f)],
        empty=_missing_fields(result),
    )
    return result
