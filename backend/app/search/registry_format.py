"""Normalise registry_id / registry_court to each country's conventional form.

Providers fetch raw registry values, and aggregators (GLEIF) and national
registers format them inconsistently — e.g. GLEIF reports the Austrian
Firmenbuchnummer as "56247t" and a French SIREN without grouping. This module
reshapes the value to the conventional national form so the output conforms to
each country's standard, e.g.:

  DE  "hrb 42243"   -> "HRB 42243"      (Handelsregister section + number)
  AT  "56247t"      -> "FN 56247 t"     (Firmenbuchnummer)
  FR  "442962239"   -> "442 962 239"    (SIREN grouped 3-3-3)
  NO  "923609016"   -> "923 609 016"    (organisasjonsnummer grouped 3-3-3)
  DE court "Local Court Munich" -> "Amtsgericht München"

Only values matching a known national pattern are reformatted; everything else
is returned whitespace-trimmed but otherwise unchanged, so unknown inputs (and
non-registry identifiers) pass through safely. All functions are total — they
never raise — so they are safe to call while constructing every SearchResult.
"""

import re

_WS = re.compile(r"\s+")


def _clean(value: str | None) -> str | None:
    return _WS.sub(" ", value).strip() if value else value


def _group(digits: str, size: int = 3) -> str:
    """Group a digit string from the left in chunks of `size` ("123456789" ->
    "123 456 789")."""
    return " ".join(digits[i : i + size] for i in range(0, len(digits), size))


# --- registry_id, per jurisdiction ------------------------------------------

# German Handelsregister: a section prefix (HRB/HRA/GnR/VR/PR), the number, and
# an optional 1-2 letter branch suffix (e.g. "HRB 42243", "HRB 1234 B").
_DE_ID = re.compile(r"^(HRB|HRA|GNR|VR|PR)\s*0*([0-9]+)\s*([A-Z]{0,2})$", re.IGNORECASE)
# Austrian Firmenbuchnummer: up to 6 digits + a single check letter, optional "FN".
_AT_ID = re.compile(r"^(?:FN\s*)?([0-9]{1,6})\s*([A-Za-z])$")


def _normalise_de_id(rid: str) -> str:
    m = _DE_ID.match(rid)
    if not m:
        return rid
    section, number, branch = m.group(1).upper(), m.group(2), m.group(3).upper()
    return f"{section} {number} {branch}".strip()


def _normalise_at_id(rid: str) -> str:
    m = _AT_ID.match(rid)
    if not m:
        return rid
    return f"FN {m.group(1)} {m.group(2).lower()}"


def _normalise_fr_id(rid: str) -> str:
    digits = rid.replace(" ", "")
    if digits.isdigit() and len(digits) == 9:  # SIREN
        return _group(digits)
    if digits.isdigit() and len(digits) == 14:  # SIRET (SIREN + 5-digit NIC)
        return f"{_group(digits[:9])} {digits[9:]}"
    return rid


def _normalise_no_id(rid: str) -> str:
    digits = rid.replace(" ", "")
    if digits.isdigit() and len(digits) == 9:  # organisasjonsnummer
        return _group(digits)
    return rid


_ID_NORMALISERS = {
    "DE": _normalise_de_id,
    "AT": _normalise_at_id,
    "FR": _normalise_fr_id,
    "NO": _normalise_no_id,
}


def normalize_registry_id(jurisdiction: str | None, registry_id: str | None) -> str | None:
    rid = _clean(registry_id)
    if not rid:
        return rid
    fn = _ID_NORMALISERS.get((jurisdiction or "").upper())
    return fn(rid) if fn else rid


# --- registry_court, per jurisdiction ---------------------------------------

# Some sources report the German court in English ("Local Court Munich"); the
# national standard is "Amtsgericht <City>".
_DE_LOCAL_COURT = re.compile(r"^local court\s+(.+)$", re.IGNORECASE)


def normalize_registry_court(jurisdiction: str | None, court: str | None) -> str | None:
    c = _clean(court)
    if not c:
        return c
    if (jurisdiction or "").upper() == "DE":
        m = _DE_LOCAL_COURT.match(c)
        if m:
            return f"Amtsgericht {m.group(1)}"
    return c


def normalize_registry(
    jurisdiction: str | None, registry_id: str | None, registry_court: str | None
) -> tuple[str | None, str | None]:
    """Return (registry_id, registry_court) reshaped to the jurisdiction's standard."""
    return (
        normalize_registry_id(jurisdiction, registry_id),
        normalize_registry_court(jurisdiction, registry_court),
    )


# --- entity status ----------------------------------------------------------

# Registers report status in different languages/labels; map them to a small
# common snake_case vocabulary (active / inactive / dissolved / in_liquidation /
# in_administration). Unknown values fall back to a snake_cased form of the input.
_STATUS_MAP = {
    "active": "active", "aktiv": "active", "registered": "active", "live": "active",
    "normal": "active", "a": "active", "i drift": "active", "issued": "active",
    "in business": "active",
    "inactive": "inactive", "lapsed": "inactive", "merged": "inactive",
    "dissolved": "dissolved", "slettet": "dissolved", "removed": "dissolved",
    "ophørt": "dissolved", "ophoert": "dissolved", "ceased": "dissolved",
    "c": "dissolved", "deregistered": "dissolved", "struck off": "dissolved",
    "strike off": "dissolved", "closed": "dissolved",
    "liquidation": "in_liquidation", "in liquidation": "in_liquidation",
    "i likvidasjon": "in_liquidation", "likvidasjon": "in_liquidation",
    "konkurs": "in_liquidation", "insolvency": "in_liquidation",
    "receivership": "in_liquidation",
    "administration": "in_administration", "in administration": "in_administration",
}


def normalize_status(status: str | None) -> str | None:
    """Map a register's status label to the common snake_case vocabulary."""
    if not status:
        return status
    key = _WS.sub(" ", status).strip().lower()
    if key in _STATUS_MAP:
        return _STATUS_MAP[key]
    return re.sub(r"[^a-z0-9]+", "_", key).strip("_") or None


# --- incorporation date -----------------------------------------------------

_ISO_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def normalize_date(value: str | None) -> str | None:
    """Reduce a date/datetime to its ISO calendar date (YYYY-MM-DD) when possible."""
    if not value:
        return value
    m = _ISO_DATE.search(value)
    return m.group(1) if m else value.strip()
