"""Deterministic company-name abbreviation expansion.

Used by the recursive-search guard: when the LLM proposes a re-query that is
identical to the original input (which would just loop), we try to turn it into a
genuinely different query by expanding the abbreviations a registry is most
likely to spell out in full — both common company-name words (``Intl`` ->
``International``) and legal/organisation-type suffixes (``GmbH`` ->
``Gesellschaft mit beschränkter Haftung``, ``Ltd`` -> ``Limited``).

This is a conservative, jurisdiction-aware fallback; the model still does the
context-sensitive expansion on the normal path (see the RECURSIVE section of the
semantic-filter system prompt).
"""

from __future__ import annotations

import re

# Legal / organisation-type suffixes whose expansion is unambiguous enough to
# apply regardless of jurisdiction.
_LEGAL_FORMS: dict[str, str] = {
    "gmbh": "Gesellschaft mit beschränkter Haftung",
    "ggmbh": "gemeinnützige Gesellschaft mit beschränkter Haftung",
    "kgaa": "Kommanditgesellschaft auf Aktien",
    "kg": "Kommanditgesellschaft",
    "ohg": "offene Handelsgesellschaft",
    "ug": "Unternehmergesellschaft",
    "gbr": "Gesellschaft bürgerlichen Rechts",
    "ag": "Aktiengesellschaft",
    "ltd": "Limited",
    "ltda": "Limitada",
    "plc": "Public Limited Company",
    "llc": "Limited Liability Company",
    "llp": "Limited Liability Partnership",
    "lp": "Limited Partnership",
    "inc": "Incorporated",
    "corp": "Corporation",
    "co": "Company",
    "sarl": "Société à Responsabilité Limitée",
    "sas": "Société par Actions Simplifiée",
    "bv": "Besloten Vennootschap",
    "nv": "Naamloze Vennootschap",
    "oy": "Osakeyhtiö",
    "oyj": "Julkinen osakeyhtiö",
    "spa": "Società per Azioni",
    "srl": "Società a Responsabilità Limitata",
}

# Suffixes whose meaning depends on the jurisdiction — disambiguate by country,
# with a sensible default when the country is unknown.
_AMBIGUOUS_FORMS: dict[str, dict[str, str]] = {
    "sa": {"FR": "Société Anonyme", "ES": "Sociedad Anónima", "PT": "Sociedade Anónima",
           "_default": "Société Anonyme"},
    "as": {"NO": "Aksjeselskap", "DK": "Aktieselskab", "_default": "Aksjeselskap"},
    "asa": {"NO": "Allmennaksjeselskap", "_default": "Allmennaksjeselskap"},
    "ab": {"SE": "Aktiebolag", "_default": "Aktiebolag"},
}

# Common abbreviated words inside a company name (not legal forms).
_NAME_WORDS: dict[str, str] = {
    "intl": "International",
    "int'l": "International",
    "natl": "National",
    "nat'l": "National",
    "mfg": "Manufacturing",
    "bros": "Brothers",
    "svcs": "Services",
    "assoc": "Associates",
    "mgmt": "Management",
    "grp": "Group",
    "hldgs": "Holdings",
    "hldg": "Holding",
    "dept": "Department",
    "dist": "Distribution",
    "&": "and",
}


def _normalize(value: str) -> str:
    """Collapse to comparable form: lowercase, alphanumerics separated by spaces."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalized_equal(a: str, b: str) -> bool:
    """True when two names are the same up to case/punctuation/whitespace."""
    return _normalize(a) == _normalize(b)


def expand_abbreviations(name: str, jurisdiction: str | None = None) -> str:
    """Expand the abbreviations in ``name`` (legal forms + common words).

    Token-wise, case-insensitive, whole-token only (so "Coca" is never touched by
    the "Co" rule). Returns the name unchanged when nothing matched.
    """
    if not name:
        return name
    juris = (jurisdiction or "").strip().upper()
    out: list[str] = []
    for token in name.split():
        core = token.strip(".,")
        key = core.lower()
        replacement: str | None = None
        if key in _AMBIGUOUS_FORMS:
            mapping = _AMBIGUOUS_FORMS[key]
            replacement = mapping.get(juris) or mapping["_default"]
        elif key in _LEGAL_FORMS:
            replacement = _LEGAL_FORMS[key]
        elif key in _NAME_WORDS:
            replacement = _NAME_WORDS[key]
        out.append(replacement if replacement else token)
    return " ".join(out)
