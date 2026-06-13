"""Impressum/legal-notice scraper — the deterministic alternative info path.

German (§5 DDG/TMG) and Austrian (§5 ECG) law require every commercial website
to publish an Impressum with the registered legal name, register number AND
register court, the registered address, and the authorised representatives —
exactly the Tier A/B fields this pipeline outputs. That makes a company's own
website a fast, precise, keyless corroboration source: one or two HTTP GETs and
a handful of regexes, no LLM call.

Flow: given the official website (from Wikidata P856 or GLEIF), try the
conventional Impressum paths, fall back to scanning the homepage for an
impressum/imprint link, then regex-extract the legally mandated facts.
Everything is total — failures return {} and never raise.
"""

import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.integrations.http import shared_client

logger = logging.getLogger(__name__)

# Conventional locations, most common first (DE, AT, generic English).
_CANDIDATE_PATHS = (
    "/impressum",
    "/impressum/",
    "/impressum.html",
    "/de/impressum",
    "/imprint",
    "/imprint/",
    "/legal-notice",
    "/legal",
    "/kontakt/impressum",
)
_LINK_WORDS = re.compile(r"impressum|imprint|legal\s*notice", re.IGNORECASE)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# Cap pathological pages, but generously: the Impressum link lives in the
# FOOTER — the end of the homepage HTML — so a tight cap cuts off exactly
# the part we scan for.
_MAX_BYTES = 4 * 1024 * 1024

# --- the legally mandated facts -------------------------------------------

# DE register number (HRA/HRB/GnR/VR/PR + number) and AT Firmenbuchnummer.
_REGISTER_ID = re.compile(
    r"\b(HRB|HRA|GnR|VR|PR)\s*\.?\s*:?\s*([0-9]{1,7})(\s*[A-Z]{1,2})?\b"
)
_AT_FN = re.compile(r"\bFN\s*:?\s*([0-9]{1,6}\s*[a-z])\b", re.IGNORECASE)
# Register court: "Amtsgericht München", "Registergericht: Amtsgericht Köln",
# "Landesgericht Salzburg", "Handelsgericht Wien".
_COURT = re.compile(
    r"\b((?:Amtsgericht|Landesgericht|Handelsgericht|Landes-\s*und\s*Handelsgericht)"
    r"\s+[A-ZÄÖÜ][\wäöüß.-]+(?:\s+[A-ZÄÖÜ][\wäöüß.-]+)?)"
)
# EU VAT id with the USt-IdNr context word nearby is reliable; a bare pattern is not.
_VAT = re.compile(
    r"(?:USt[.\s-]*Id(?:Nr)?\.?|Umsatzsteuer-?Identifikationsnummer|VAT)[^A-Z0-9]{0,15}"
    r"\b([A-Z]{2}\s?U?\d{8,11})\b",
    re.IGNORECASE,
)
# Representatives: "Geschäftsführer: Max Mustermann", "Vorstand: ...", "Inhaber: ...".
# Names must stay on one line — `[ \t]` (not \s) so the capture cannot run
# across the line break into the next Impressum section.
_OFFICERS = re.compile(
    r"\b(Geschäftsführer(?:in|innen)?|Vorstand|Vorstandsvorsitzende[r]?|Inhaber(?:in)?|"
    r"Vertretungsberechtigte[r]?(?:\s+Geschäftsführer)?|Managing\s+Directors?)[ \t]*:?[ \t]*"
    r"([A-ZÄÖÜ][\wäöüß.-]+(?:[ \t]+[A-ZÄÖÜ][\wäöüß.-]+){1,3}"
    r"(?:[ \t]*,[ \t]*[A-ZÄÖÜ][\wäöüß.-]+(?:[ \t]+[A-ZÄÖÜ][\wäöüß.-]+){1,3}){0,8})"
)
# A German/Austrian address line: "Petuelring 130" newline-ish "80809 München".
_ADDRESS = re.compile(
    r"([A-ZÄÖÜ][\wäöüß.\- ]{2,40}\s\d{1,4}[a-z]?)\s*[,\n|]\s*([A-Z]{0,2}[-\s]?\d{4,5}\s+[A-ZÄÖÜ][\wäöüß.\- ]{2,40})"
)


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,;:")


def extract_company_facts(text: str) -> dict:
    """Regex-extract the legally mandated Impressum facts from page text."""
    facts: dict = {}
    m = _REGISTER_ID.search(text)
    if m:
        facts["registry_id"] = _clean(
            f"{m.group(1).upper()} {m.group(2)}{(' ' + m.group(3).strip()) if m.group(3) else ''}"
        )
    else:
        m = _AT_FN.search(text)
        if m:
            facts["registry_id"] = f"FN {_clean(m.group(1))}"
    m = _COURT.search(text)
    if m:
        # The court name often runs straight into the register section in the
        # page text ("Amtsgericht Paderborn HRB 10033") — cut it off there.
        court = re.sub(r"\s+(HRB|HRA|GnR|VR|PR|FN)\b.*$", "", m.group(1))
        facts["registry_court"] = _clean(court)
    m = _VAT.search(text)
    if m:
        facts["vat_number"] = m.group(1).replace(" ", "")
    m = _OFFICERS.search(text)
    if m:
        role, names = _clean(m.group(1)), _clean(m.group(2))
        facts["officers"] = "; ".join(
            f"{role}: {name.strip()}" for name in names.split(",") if name.strip()
        )
    m = _ADDRESS.search(text)
    if m:
        facts["registered_address"] = f"{_clean(m.group(1))}, {_clean(m.group(2))}"
    return facts


async def _fetch_text(url: str) -> str | None:
    client = shared_client(
        "impressum",
        timeout=10.0,
        follow_redirects=True,
        headers={"User-Agent": _UA, "Accept-Language": "de,en;q=0.8"},
    )
    try:
        resp = await client.get(url)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    return resp.text[:_MAX_BYTES]


def _page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


async def fetch_impressum(website: str) -> tuple[str, dict] | None:
    """Locate the Impressum page of `website` and extract its facts.

    Returns (impressum_url, facts) or None when no usable page was found.
    """
    parsed = urlparse(website if "://" in website else f"https://{website}")
    if not parsed.netloc:
        return None
    base = f"{parsed.scheme}://{parsed.netloc}"

    candidates = [urljoin(base, path) for path in _CANDIDATE_PATHS]
    # Fall back to whatever the homepage links as Impressum/imprint. The homepage
    # fetch must come first — it is what reveals that linked URL.
    homepage = await _fetch_text(base)
    if homepage:
        soup = BeautifulSoup(homepage, "html.parser")
        for a in soup.find_all("a", href=True):
            if _LINK_WORDS.search(a.get_text(" ", strip=True) or "") or _LINK_WORDS.search(
                a["href"]
            ):
                candidates.append(urljoin(base, a["href"]))
                break

    # De-dup while preserving priority order, then fetch all candidates
    # CONCURRENTLY (each was a separate sequential 10s GET before). Selection
    # semantics are unchanged: walk the candidates in priority order and return
    # the FIRST that yields a register fact.
    ordered: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        if url not in seen:
            seen.add(url)
            ordered.append(url)

    pages = await asyncio.gather(*(_fetch_text(url) for url in ordered))
    for url, html in zip(ordered, pages):
        if not html:
            continue
        facts = extract_company_facts(_page_text(html))
        # A page is only an Impressum hit if it yields a register fact, not
        # merely an address (every contact page has one of those).
        if facts.get("registry_id") or facts.get("registry_court") or facts.get("officers"):
            return url, facts
    return None
