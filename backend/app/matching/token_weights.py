"""Fellegi-Sunter term-frequency token weighting (technique ported from Splink).

Splink's record-linkage model gives rare, discriminative agreements far more
weight than common ones (its term-frequency adjustment). We port that idea to
company-name scoring *without* Splink's batch/EM machinery: weight each token by
its inverse frequency over the candidate set, and down-weight a curated set of
generic/legal-form tokens. A shared core name ("siemens") then dominates the
score while legal forms and corporate filler ("gmbh", "group", "holding",
"international") count for little.

The public scorer :func:`weighted_token_score` is a weighted token-set F1 with
per-token fuzzy matching, so it is robust to token re-ordering, extra tokens, and
small typos — and complements RapidFuzz rather than replacing it.
"""

from __future__ import annotations

import math
import unicodedata

from rapidfuzz import fuzz, utils

# Generic / legal-form / corporate-filler tokens — little identifying value, so
# heavily down-weighted. Covers abbreviations AND full-word legal forms across
# the jurisdictions we gather from.
_GENERIC_TOKENS = frozenset(
    {
        # legal-form abbreviations
        "gmbh", "ag", "se", "kg", "kgaa", "ohg", "ug", "gbr", "mbh", "ggmbh", "co",
        "inc", "incorporated", "corp", "corporation", "company", "limited", "ltd",
        "plc", "llc", "llp", "lp", "pllc", "pc", "pty", "dac", "clg", "ulc",
        "sa", "sas", "sasu", "sarl", "eurl", "snc", "sci", "scop",
        "sl", "slu", "srl", "srls", "spa", "ltda", "bv", "bvba", "nv", "cvba",
        "vof", "cv", "oy", "oyj", "ab", "abp", "asa", "aps", "as", "hb", "kb",
        "sp", "zoo", "spzoo", "sro", "kft", "zrt", "nyrt", "bt", "ooo", "oao",
        "zao", "pao", "doo", "jsc", "pjsc", "cjsc", "kk", "gk", "pte", "pvt",
        "sdn", "bhd", "ev", "eg",
        # full-word legal forms
        "aktiengesellschaft", "gesellschaft", "kommanditgesellschaft", "haftung",
        "beschrankter", "offene", "handelsgesellschaft", "eingetragener",
        "kaufmann", "verein", "genossenschaft", "stiftung", "anstalt",
        "societe", "anonyme", "responsabilite", "limitee", "aktiebolag",
        "osakeyhtio", "naamloze", "vennootschap", "besloten", "sociedad",
        "anonima", "limitada", "kabushiki", "kaisha",
        # generic corporate filler
        "group", "holding", "holdings", "international", "global", "services",
        "service", "solutions", "systems", "technologies", "technology",
        "industries", "industrie", "enterprises", "enterprise", "ventures",
        "capital", "partners", "trading", "consulting", "management",
        # stop-words / connectives
        "and", "und", "the", "of", "for", "et", "de", "la", "le", "y", "e", "an",
    }
)

# Generic tokens count at this fraction of a fully-discriminative token.
_GENERIC_WEIGHT = 0.2
# Default weight for a token unseen in the candidate set (e.g. query-only).
_UNSEEN_WEIGHT = 1.0
# Per-token fuzzy ratio (0..1) above which two tokens are "the same token".
_TOKEN_MATCH = 0.82


def _ascii_fold(text: str) -> str:
    """Strip diacritics so "Nestlè" == "Nestle", "München" == "Muenchen"-ish."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def tokens(text: str | None) -> list[str]:
    return utils.default_process(_ascii_fold(text or "")).split()


def discriminative_core(name: str | None) -> str:
    """The name with generic/legal tokens removed — its identifying core.

    "Siemens Aktiengesellschaft", "Siemens AG" and "Siemens" all reduce to
    "siemens", so they cluster as one entity.
    """
    core = [t for t in tokens(name) if t not in _GENERIC_TOKENS]
    return " ".join(core) or " ".join(tokens(name))


def token_weights(names: list[str]) -> dict[str, float]:
    """Inverse-frequency weight per token over the candidate set (FS-style)."""
    docs = [set(tokens(n)) for n in names]
    n_docs = max(len(docs), 1)
    df: dict[str, int] = {}
    for doc in docs:
        for t in doc:
            df[t] = df.get(t, 0) + 1
    weights: dict[str, float] = {}
    for t, count in df.items():
        idf = math.log((n_docs + 1) / (count + 0.5)) + 1.0  # smoothed, always > 0
        if t in _GENERIC_TOKENS:
            idf *= _GENERIC_WEIGHT
        weights[t] = idf
    return weights


def _weight(token: str, weights: dict[str, float]) -> float:
    if token in weights:
        return weights[token]
    return _GENERIC_WEIGHT if token in _GENERIC_TOKENS else _UNSEEN_WEIGHT


def _coverage(a: list[str], b: list[str], weights: dict[str, float]) -> float:
    """Weighted recall of ``a``'s tokens given ``b`` (fuzzy per-token match)."""
    total = 0.0
    matched = 0.0
    for t in a:
        w = _weight(t, weights)
        total += w
        best = max((fuzz.ratio(t, u) / 100.0 for u in b), default=0.0)
        if best >= _TOKEN_MATCH:
            matched += w * best
    return matched / total if total else 0.0


def weighted_token_score(query: str, name: str, weights: dict[str, float]) -> float:
    """Weighted token-set F1 between two names (Fellegi-Sunter flavour).

    Precision and recall are each weighted by token inverse-frequency, so an
    extra *discriminative* token on either side lowers the score while extra
    *generic* tokens (legal forms, "group", …) barely move it.
    """
    q = tokens(query)
    c = tokens(name)
    if not q or not c:
        return 0.0
    recall = _coverage(q, c, weights)     # are the query's tokens in the candidate?
    precision = _coverage(c, q, weights)  # are the candidate's tokens in the query?
    if precision + recall == 0.0:
        return 0.0
    return round(2.0 * precision * recall / (precision + recall), 4)


def weighted_containment_score(query: str, name: str, weights: dict[str, float]) -> float:
    """Asymmetric: is the QUERY *contained* in the candidate name?

    Weighted recall of the query's tokens — extra text in the candidate is NOT
    penalised. So "Nestle" -> "Nestle S.A." and "Nestle" -> "Nestle Deutschland
    GmbH" both score ~1.0, which is what a recall-oriented gross filter wants
    (precision is the LLM's and fame's job downstream). The query's *own*
    discriminative tokens must be present, so "ABC Group" does NOT match
    "XYZ Group".
    """
    q = tokens(query)
    c = tokens(name)
    if not q or not c:
        return 0.0
    return round(_coverage(q, c, weights), 4)
