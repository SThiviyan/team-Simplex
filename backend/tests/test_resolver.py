"""Hermetic tests for the jurisdiction-aware, specificity-weighted resolver."""

from app.search.base import SearchProvider, SearchResult
from app.search.resolver import CompanyResolver, _looks_like_abbreviation, _specificity


class _Fake(SearchProvider):
    def __init__(self, name, results, jurisdictions=None):
        self.name = name
        self.jurisdictions = jurisdictions
        self._results = results

    async def search(self, query, limit=10):
        return self._results


def _r(title, score, source, sitelinks=0):
    return SearchResult(
        title=title, snippet="", score=score, source=source, metadata={"sitelinks": sitelinks}
    )


def test_query_classification():
    assert _looks_like_abbreviation("UBS")
    assert _looks_like_abbreviation("IBM")
    assert not _looks_like_abbreviation("Apple")
    assert not _looks_like_abbreviation("BASF SE")
    assert _specificity("UBS") == 0.0
    assert _specificity("Bayerische Motoren Werke AG") > 0.7


def test_jurisdiction_routing_skips_irrelevant_national_source():
    providers = [
        _Fake("global", []),
        _Fake("de_register", [], jurisdictions={"DE"}),
    ]
    resolver = CompanyResolver(providers)
    # No need for network; just assert selection via the public result fields.
    import asyncio

    out = asyncio.run(resolver.resolve("Foo", jurisdiction="HU"))
    assert "de_register" in out["sources_skipped"]
    assert "global" in out["sources_called"]


async def test_abbreviation_prefers_bigger_company():
    # Two same-named clusters; one is far more prominent (sitelinks).
    gleif = _Fake("gleif", [_r("UBS AG (LEI1)", 1.0, "gleif")])
    wikidata = _Fake(
        "wikidata",
        [_r("UBS (Q1)", 1.0, "wikidata", sitelinks=80), _r("UBS Gold (Q2)", 1.0, "wikidata", sitelinks=0)],
    )
    out = await CompanyResolver([gleif, wikidata]).resolve("UBS")
    assert out["query_kind"] == "abbreviation"
    assert out["most_likely"]["prominence"] == 1.0  # the big one won


async def test_small_business_not_discarded_when_no_large_name_match():
    # A big but tangentially-named entity must NOT bury a small exact match.
    gleif = _Fake("gleif", [_r("ZXC (LEI1)", 1.0, "gleif")])  # small, exact, no sitelinks
    wikidata = _Fake("wikidata", [_r("ZXC News Network (Q1)", 0.7, "wikidata", sitelinks=120)])
    out = await CompanyResolver([gleif, wikidata]).resolve("ZXC")
    assert out["query_kind"] == "abbreviation"
    # The small exact match wins because the big one doesn't match the name.
    assert out["most_likely"]["name"].startswith("ZXC (")
    assert out["most_likely"]["prominence"] == 0.0


async def test_big_company_still_wins_when_it_matches_the_name():
    gleif = _Fake("gleif", [_r("ZXC Ltd (LEI1)", 1.0, "gleif")])  # small
    wikidata = _Fake("wikidata", [_r("ZXC (Q1)", 1.0, "wikidata", sitelinks=120)])  # big, exact
    out = await CompanyResolver([gleif, wikidata]).resolve("ZXC")
    assert out["most_likely"]["sitelinks"] == 120  # the prominent exact match wins


async def test_specific_name_prefers_name_match_over_prominence():
    # A prominent but loosely-named entity vs an exact long-name match.
    gleif = _Fake("gleif", [_r("Acme Global Industries Corporation (LEI)", 1.0, "gleif")])
    wikidata = _Fake("wikidata", [_r("Acme (Q9)", 1.0, "wikidata", sitelinks=90)])
    out = await CompanyResolver([gleif, wikidata]).resolve("Acme Global Industries Corporation")
    assert out["query_kind"] == "specific-name"
    assert out["most_likely"]["name"].startswith("Acme Global Industries")
