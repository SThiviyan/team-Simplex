import csv

import pytest

from app.config import settings
from app.pipeline import event_log
from app.pipeline.csv_io import RESULT_COLUMNS, read_queries, read_queries_text
from app.pipeline.mcp_registry import get_mcp_servers
from app.pipeline.models import ExtractionPayload, ExtractionResult, QueryRow
from app.pipeline.runner import run_pipeline


@pytest.fixture
def mock_mode():
    original = settings.pipeline_mock
    settings.pipeline_mock = True
    yield
    settings.pipeline_mock = original


@pytest.fixture(autouse=True)
def isolated_event_db(tmp_path):
    event_log.configure(tmp_path / "events.db")
    yield
    event_log.configure(event_log.DEFAULT_DB_PATH)


@pytest.fixture(autouse=True)
def empty_search_cache():
    from app.search import search_cache

    search_cache.clear()
    yield
    search_cache.clear()


def test_read_queries_skips_comments():
    queries = read_queries()
    assert len(queries) >= 3
    assert all(isinstance(q, QueryRow) for q in queries)
    assert queries[0].query_id == "Q-001"


def test_read_queries_autodetects_comma_delimiter():
    text = "query_id,name,jurisdiction\np001,Tesco PLC,UK\np002, Sinpex GmbH ,DE\n"
    queries = read_queries_text(text)
    assert [q.query_id for q in queries] == ["p001", "p002"]
    assert queries[1].name == "Sinpex GmbH"  # values are whitespace-stripped


def test_read_queries_handles_semicolons_and_bom():
    text = "﻿query_id;name;jurisdiction\nh001;Acme GmbH & Co. KG;DE\n"
    queries = read_queries_text(text.lstrip("﻿"))
    assert queries[0].name == "Acme GmbH & Co. KG"


def test_mcp_registry_routes_to_internal_endpoints():
    de = get_mcp_servers("de")
    # CSV placeholders first, then the built-in per-country endpoint baseline.
    assert de[-1].url == "internal:de"
    assert all(e.is_placeholder for e in de[:-1])

    # Countries with a national provider but no CSV get their bucket directly.
    assert get_mcp_servers("FR")[-1].url == "internal:fr"
    # State-level codes route to the parent country's bucket.
    assert get_mcp_servers("US-CA")[-1].url == "internal:us"
    # Uncovered countries land on the global bucket (GLEIF/Wikidata).
    assert get_mcp_servers("UK")[-1].url == "internal:gb"  # alias routes to GB bucket
    assert get_mcp_servers("CH")[-1].url == "internal:global"


async def test_country_alias_normalization():
    from app.search.base import SearchProvider, SearchResult, normalize_country
    from app.search.csv_search import parse_query_csv, search_jurisdiction

    assert normalize_country("uk") == "GB"
    assert normalize_country("UK") == "GB"
    assert normalize_country("EL") == "GR"
    assert normalize_country("DE") == "DE"
    assert normalize_country("  ") is None

    # Trailing-jurisdiction detection accepts the alias too.
    assert parse_query_csv("Tesco UK") == [("Tesco", "GB")]

    # The jurisdiction filter keeps GB records when the user typed "UK".
    class StubProvider(SearchProvider):
        name = "stub"
        jurisdictions = None

        async def search(self, query, limit=10):
            return [
                SearchResult(
                    title="TESCO PLC", snippet="", score=0.9, source="stub",
                    jurisdiction="GB", registry_id="00445790",
                )
            ]

    results, called, _ = await search_jurisdiction([StubProvider()], "Tesco", "UK", 10)
    assert len(results) == 1 and results[0].jurisdiction == "GB"


async def test_search_cache_dedupes_identical_queries():
    from app.search.base import SearchProvider, SearchResult
    from app.search.csv_search import search_jurisdiction

    class CountingProvider(SearchProvider):
        name = "counting"
        jurisdictions = None
        calls = 0

        async def search(self, query, limit=10):
            CountingProvider.calls += 1
            return [
                SearchResult(
                    title="ACME GmbH", snippet="", score=0.9, source="counting",
                    jurisdiction="DE", registry_id="HRB 1",
                )
            ]

    provider = CountingProvider()
    r1, _, _ = await search_jurisdiction([provider], "Acme", "DE", 10)
    r2, _, _ = await search_jurisdiction([provider], "ACME ", "DE", 10)  # same after normalize
    assert CountingProvider.calls == 1  # second call served from cache
    assert r1 == r2
    # Different query or limit -> live call
    await search_jurisdiction([provider], "Acme", "DE", 20)
    assert CountingProvider.calls == 2


async def test_search_cache_never_caches_failures():
    from app.search.base import SearchProvider
    from app.search.search_cache import cached_search

    class FlakyProvider(SearchProvider):
        name = "flaky"
        jurisdictions = None
        calls = 0

        async def search(self, query, limit=10):
            FlakyProvider.calls += 1
            if FlakyProvider.calls == 1:
                raise RuntimeError("registry down")
            return []

    provider = FlakyProvider()
    with pytest.raises(RuntimeError):
        await cached_search(provider, "Acme", 10)
    assert await cached_search(provider, "Acme", 10) == []  # retried live, then cached
    assert FlakyProvider.calls == 2


def test_grounding_blanks_unbacked_registry_ids():
    from app.pipeline.agent import UNGROUNDED_REASON, apply_grounding

    payload = ExtractionPayload(
        registry_id="HRB 99999",
        registry_court="Amtsgericht Nirgendwo",
        name_normalized_register_name="Phantom GmbH",
        jurisdiction_confirmed="DE",
        confidence=0.95,
        source="https://example.com",
        no_match_reason=None,
        registered_address=None,
        incorporation_date=None,
        organization_type=None,
        status=None,
        officers=None,
        reasoning="looks right",
    )
    trace = [{"tool": "search_companies", "output": '{"results": [{"registry_id": "HRB 1234"}]}'}]

    blanked, grounded = apply_grounding(payload, trace)
    assert not grounded
    assert blanked.registry_id is None
    assert blanked.no_match_reason == UNGROUNDED_REASON
    assert blanked.confidence == 0.0

    grounded_payload = payload.model_copy(update={"registry_id": "HRB 1234"})
    kept, grounded = apply_grounding(grounded_payload, trace)
    assert grounded and kept.registry_id == "HRB 1234"


def test_grounding_replaces_fabricated_source_urls():
    from app.pipeline.agent import apply_grounding

    payload = ExtractionPayload(
        registry_id="HRB 1234",
        registry_court="Amtsgericht München",
        name_normalized_register_name="Echt GmbH",
        jurisdiction_confirmed="DE",
        confidence=0.9,
        source="https://made-up.example/echt-gmbh",
        no_match_reason=None,
        registered_address=None,
        incorporation_date=None,
        organization_type=None,
        status=None,
        officers=None,
        reasoning="match",
    )
    trace = [{"tool": "search_companies", "output": '{"results": [{"registry_id": "HRB 1234"}]}'}]

    # The ID is grounded, but the cited URL never appeared in a tool result:
    # it is swapped for the registry document reference (court + number).
    kept, grounded = apply_grounding(payload, trace)
    assert grounded and kept.registry_id == "HRB 1234"
    assert kept.source == "Amtsgericht München HRB 1234"

    # A URL that DID appear in a tool result passes through untouched.
    url = "https://search.gleif.org/#/record/X"
    trace_with_url = trace + [{"tool": "search_companies", "output": f'{{"url": "{url}"}}'}]
    kept, _ = apply_grounding(payload.model_copy(update={"source": url}), trace_with_url)
    assert kept.source == url

    # Non-URL document references are not URL-checked.
    doc_ref = payload.model_copy(update={"source": "Amtsgericht München HRB 1234"})
    kept, _ = apply_grounding(doc_ref, trace)
    assert kept.source == "Amtsgericht München HRB 1234"


def test_mcp_registry_falls_back_when_country_csv_is_missing():
    # US/BR/AT are mapped to CSV files that don't exist yet — they must fall
    # back to the extra_eu list instead of silently getting no external entries.
    for cc, bucket in (("US", "us"), ("BR", "global"), ("AT", "global")):
        entries = get_mcp_servers(cc)
        assert entries[-1].url == f"internal:{bucket}"
        external = entries[:-1]
        assert external, f"{cc} should inherit the extra_eu fallback entries"
        assert all(e.is_placeholder for e in external)


def _pwc_records():
    """The 13 real records the gather layer returned for 'PwC, DE' (live data)."""
    names = [
        ("Betriebssport-Gemeinschaft PricewaterhouseCoopers Essen e. V.", "VR 4195"),
        ("Konsortium PwC Deutschland eGbR", None),
        ("PwC Strategy& (Germany) GmbH", "529900W1ZP04CIGY9R95"),
        ("PwC IT Services Europe GmbH", "5299007RQMVQI2UO8U72"),
        ("PWC Gesellschaft für medizinische Testverfahren im Sport mbH", "HRB 105486"),
        ("PWC Holding GmbH", "HRB 20859"),
        ("PWC Holding GmbH", "HRB 206501"),
        ("Pflegedienst PWC Pflegen und Wohlfühlen Care OHG", "HRA 98305"),
        ("PricewaterhouseCoopers Europe GmbH", "HRB 125771"),
        ("PwC Advisory Europe GmbH", "HRB 126545"),
        ("PwC Certification Services GmbH", "HRB 132988"),
        ("PwC Cyber Security Services GmbH", "HRB 116391"),
        ("Betriebskrankenkasse PricewaterhouseCoopers (BKK PwC)", "391200KAGMBFDCND5182"),
    ]
    return [
        {
            "query_id": "q1",
            "registry_id": rid,
            "registry_court": None,
            "name_normalized_register_name": name,
            "jurisdiction_confirmed": "DE",
            "confidence": 0.6,
            "source": "https://example.test",
            "no_match_reason": None,
        }
        for name, rid in names
    ]


def test_abbreviation_query_survives_gross_filter():
    """'PwC' must not lose all 13 gathered PwC records to the fuzzy cutoff."""
    from app.matching.pipeline import run_matching

    result = run_matching(_pwc_records(), "PwC", "DE", mock=True)
    assert result["query_kind"] == "abbreviation"
    assert result["gross_filter"]["scorer"] == "token_sort+partial"
    assert result["gross_filter"]["candidates_kept"] > 0
    assert result["decision"] == "match"
    assert "pwc" in result["winning_candidate"]["name_normalized_register_name"].lower() or (
        "pricewaterhouse" in result["winning_candidate"]["name_normalized_register_name"].lower()
    )


def test_specific_name_query_keeps_strict_filter():
    from app.matching.pipeline import run_matching

    result = run_matching(
        _pwc_records(), "PricewaterhouseCoopers Europe GmbH", "DE", mock=True
    )
    assert result["query_kind"] == "specific-name"
    assert result["gross_filter"]["scorer"] == "token_sort"
    assert result["decision"] == "match"
    assert result["winning_candidate"]["registry_id"] == "HRB 125771"


async def test_agent_first_round_forces_tool_use(monkeypatch):
    """The agent must search before answering — round 0 carries tool_choice=any."""
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.pipeline import agent
    from app.pipeline.models import McpServerEntry

    payload_json = json.dumps(
        {
            "registry_id": None, "registry_court": None,
            "name_normalized_register_name": None, "jurisdiction_confirmed": None,
            "confidence": 0.0, "source": None,
            "no_match_reason": "not_in_registry", "reasoning": "test",
            "registered_address": None, "incorporation_date": None,
            "organization_type": None, "status": None, "officers": None,
        }
    )

    calls: list[dict] = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:  # forced search round -> pretend a tool was used
                return SimpleNamespace(
                    stop_reason="tool_use",
                    content=[SimpleNamespace(type="tool_use", id="t1",
                                             name="search_companies", input={"name": "x"})],
                )
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=payload_json)],
            )

    class FakeSession:
        async def list_tools(self):
            return SimpleNamespace(tools=[])

    @asynccontextmanager
    async def fake_open_session(url, auth_token=None):
        yield FakeSession()

    async def fake_call_tool(session, name, arguments):
        return '{"results": []}', {"results": []}

    monkeypatch.setattr(agent, "open_session", fake_open_session)
    monkeypatch.setattr(agent, "call_tool", fake_call_tool)

    outcome = agent.Layer1Outcome()
    query = QueryRow(query_id="t1", name="PwC", jurisdiction="DE")
    entry = McpServerEntry(rank=1, name="test", url="internal:de")
    result = await agent._mcp_attempt(
        SimpleNamespace(messages=FakeMessages()), query, entry, outcome, "test-run"
    )

    assert calls[0]["tool_choice"] == {"type": "any"}
    assert "output_config" not in calls[0]
    assert "tool_choice" not in calls[1]
    assert "output_config" in calls[1]
    assert result is not None and result.no_match_reason == "not_in_registry"
    assert len(outcome.trace) == 1  # the forced tool call was traced


async def test_layer1_surfaces_attempt_errors(monkeypatch):
    """If every Layer-1 attempt raises, the error must surface in no_match_reason
    instead of masquerading as 'no candidates' / 'no match'."""
    from app.pipeline import agent

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # client init only; no call happens

    async def overloaded(*args, **kwargs):
        raise RuntimeError("simulated 529 overloaded_error")

    monkeypatch.setattr(agent, "_mcp_attempt", overloaded)
    monkeypatch.setattr(agent, "_web_search_attempt", overloaded)

    query = QueryRow(query_id="err-1", name="Tesco", jurisdiction="UK")
    outcome = await agent.run_layer1(query, get_mcp_servers("UK"), run_id="test-run")

    error_row = outcome.error_result("err-1")
    assert error_row is not None
    assert error_row.no_match_reason.startswith("layer1_error: ")
    assert "simulated 529" in error_row.no_match_reason
    assert error_row.confidence == 0.0


async def test_recursion_reenters_layer1(monkeypatch, mock_mode):
    from app.pipeline import runner
    from app.pipeline.agent import Layer1Outcome

    gathered_names: list[str] = []

    async def fake_layer1(query, mcps, run_id):
        gathered_names.append(query.name)
        outcome = Layer1Outcome()
        outcome.candidates.append(ExtractionResult(query_id=query.query_id, confidence=0.3))
        outcome.records.append(
            {
                "query_id": query.query_id,
                "registry_id": "HRB 1",
                "registry_court": "AG Test",
                "name_normalized_register_name": f"{query.name} AG",
                "jurisdiction_confirmed": "DE",
                "confidence": 0.5,
                "source": "https://reg.example/1",
                "no_match_reason": None,
            }
        )
        return outcome

    matches = [
        {
            "decision": "recursive_search",
            "winning_candidate": None,
            "confidence": 0.4,
            "reasoning": "query looks like an acronym",
            "recursive_search": {"suggested_query": "Bayerische Motoren Werke"},
            "candidates": [],
        },
        {
            "decision": "match",
            "winning_candidate": {
                "registry_id": "HRB 42243",
                "registry_court": "Amtsgericht München",
                "name_normalized_register_name": "Bayerische Motoren Werke AG",
                "jurisdiction_confirmed": "DE",
                "source": "https://reg.example/bmw",
            },
            "confidence": 0.93,
            "reasoning": "expanded acronym matches",
            "recursive_search": None,
            "candidates": [{}],
        },
    ]

    async def fake_matching(query, records, run_id):
        return matches.pop(0)

    monkeypatch.setattr(runner, "run_layer1", fake_layer1)
    monkeypatch.setattr(runner, "_run_matching", fake_matching)

    result = await runner.process_query(
        QueryRow(query_id="r1", name="BMW", jurisdiction="DE"), run_id="test-run"
    )

    assert gathered_names == ["BMW", "Bayerische Motoren Werke"]  # one recursion round
    assert result.registry_id == "HRB 42243"
    # Layer 2 calibration: a single-source (non-registry-provider) ID is capped
    # at the 'probable' ceiling and flagged as such.
    assert result.confidence_flag == "probable"
    assert result.confidence == 0.85


async def test_matching_path_runs_and_logs_filter_stats(mock_mode, monkeypatch):
    """Low-confidence agent answer with gathered records must flow through the
    REAL matching layer (mock LLM) and log filter stats without crashing.
    Regression: filter_result event once raised TypeError on duplicate kwargs."""
    from app.pipeline import runner
    from app.pipeline.agent import Layer1Outcome

    async def fake_layer1(query, mcps, run_id):
        outcome = Layer1Outcome()
        outcome.candidates.append(
            ExtractionResult(query_id=query.query_id, registry_id="HRB 1", confidence=0.4)
        )
        outcome.records.append(
            {
                "query_id": query.query_id,
                "registry_id": "HRB 6684",
                "registry_court": "Amtsgericht München",
                "name_normalized_register_name": "Siemens Aktiengesellschaft",
                "jurisdiction_confirmed": "DE",
                "confidence": 0.7,
                "source": "https://reg.example/siemens",
                "no_match_reason": None,
            }
        )
        return outcome

    monkeypatch.setattr(runner, "run_layer1", fake_layer1)

    result = await runner.process_query(
        QueryRow(query_id="m1", name="Siemens AG", jurisdiction="DE"), run_id="match-run"
    )
    assert not (result.no_match_reason or "").startswith("pipeline_error")
    assert result.registry_id == "HRB 6684"

    types = [e["event_type"] for e in event_log.list_events("match-run")]
    assert "eval_started" in types and "eval_result" in types and "filter_result" in types


async def test_pipeline_mock_end_to_end(mock_mode, tmp_path):
    summary = await run_pipeline(limit=2, output_dir=tmp_path)

    assert summary.rows_processed == 2
    assert summary.run_id
    assert all(r.confidence > 0 for r in summary.results)

    # Output is a clean semicolon-delimited CSV: header + one row per query, no comments.
    with open(summary.output_csv, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert not any(line.startswith("#") for line in lines)
    rows = list(csv.DictReader(lines, delimiter=";"))
    assert list(rows[0].keys()) == RESULT_COLUMNS
    assert len(rows) == 2
    assert rows[0]["query_id"] == "Q-001"


async def test_event_log_records_run_sequence(mock_mode, tmp_path):
    summary = await run_pipeline(limit=1, output_dir=tmp_path)

    events = event_log.list_events(summary.run_id)
    types = [e["event_type"] for e in events]
    assert types[0] == "run_started"
    assert types[-1] == "run_completed"
    assert "query_started" in types and "query_completed" in types
    assert "agent_answer" in types and "eval_skipped" in types
    # seq strictly increasing; incremental polling works
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    later = event_log.list_events(summary.run_id, after=seqs[1])
    assert [e["seq"] for e in later] == seqs[2:]


async def test_pipeline_preserves_input_order_and_isolates_crashes(mock_mode, tmp_path, monkeypatch):
    from app.pipeline import runner

    real_process = runner.process_query

    async def flaky(query, run_id):
        if query.query_id == "p002":
            raise RuntimeError("boom on row 2")
        return await real_process(query, run_id)

    monkeypatch.setattr(runner, "process_query", flaky)

    queries = [
        QueryRow(query_id=f"p{i:03d}", name=f"Company {i}", jurisdiction="DE") for i in range(1, 5)
    ]
    summary = await run_pipeline(queries=queries, output_dir=tmp_path)

    # Order matches input despite concurrency; the crashed row is an honest error row.
    assert [r.query_id for r in summary.results] == ["p001", "p002", "p003", "p004"]
    assert summary.results[1].no_match_reason.startswith("pipeline_error: RuntimeError")
    assert summary.results[1].confidence == 0.0
    assert all(r.confidence > 0 for i, r in enumerate(summary.results) if i != 1)


def test_run_csv_upload_endpoint(mock_mode):
    from fastapi.testclient import TestClient

    from app.main import app

    csv_bytes = b"query_id,name,jurisdiction\np001,Tesco PLC,UK\np002,Sinpex GmbH,DE\n"
    with TestClient(app) as client:
        resp = client.post(
            "/api/pipeline/run-csv",
            files={"file": ("test.csv", csv_bytes, "text/csv")},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")

    rows = list(csv.DictReader(resp.text.splitlines(), delimiter=";"))
    assert list(rows[0].keys()) == RESULT_COLUMNS
    assert [r["query_id"] for r in rows] == ["p001", "p002"]


def test_runs_and_events_endpoints(mock_mode):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/pipeline/run",
            json={"query": {"query_id": "ui-1", "name": "Sinpex GmbH", "jurisdiction": "DE"}},
        )
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]

        runs = client.get("/api/pipeline/runs").json()["runs"]
        assert any(r["run_id"] == run_id and r["status"] == "completed" for r in runs)

        feed = client.get(f"/api/pipeline/runs/{run_id}/events").json()
        assert feed["events"][0]["event_type"] == "run_started"
        assert feed["last_seq"] == feed["events"][-1]["seq"]
        # incremental cursor returns only newer events
        rest = client.get(
            f"/api/pipeline/runs/{run_id}/events", params={"after": feed["events"][0]["seq"]}
        ).json()
        assert len(rest["events"]) == len(feed["events"]) - 1


def test_run_csv_upload_rejects_garbage(mock_mode):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/pipeline/run-csv", files={"file": ("x.csv", b"", "text/csv")})
    assert resp.status_code == 400


def test_grounding_blanks_non_registry_identifier_shapes():
    """LEI / Wikidata QID / SEC CIK are grounded in tool output but are not
    registration numbers — they must be blanked with their own reason."""
    from app.pipeline.agent import NON_REGISTRY_REASON, apply_grounding

    def payload_with(rid):
        return ExtractionPayload(
            registry_id=rid,
            registry_court=None,
            name_normalized_register_name="Some AG",
            jurisdiction_confirmed="DE",
            confidence=0.9,
            source="https://example.org",
            no_match_reason=None,
            registered_address=None,
            incorporation_date=None,
            organization_type=None,
            status=None,
            officers=None,
            reasoning="r",
        )

    for bad in ("2138002P5RNKC5W2JZ46", "Q552581", "0000320193"):
        trace = [{"tool": "search_companies", "output": f'{{"id": "{bad}"}}'}]
        blanked, grounded = apply_grounding(payload_with(bad), trace)
        assert not grounded, bad
        assert blanked.registry_id is None
        assert blanked.no_match_reason == NON_REGISTRY_REASON
        assert blanked.confidence == 0.0
        # Also caught on the web-search path, where trace grounding is skipped.
        blanked_web, grounded_web = apply_grounding(
            payload_with(bad), [], web_search=True
        )
        assert not grounded_web and blanked_web.registry_id is None

    # Real registration numbers of similar flavours must pass.
    for good in ("HRB 1234", "CHE-105.909.036", "00445790", "FN 56247t", "0112038-9"):
        trace = [{"tool": "search_companies", "output": f'{{"id": "{good}"}}'}]
        kept, grounded = apply_grounding(payload_with(good), trace)
        assert grounded and kept.registry_id == good, good


async def test_salvage_records_when_agent_call_dies(monkeypatch, mock_mode):
    """A 529-killed agent must not discard gathered records: the matching layer
    runs over them instead of emitting a layer1_error row."""
    from app.pipeline import runner
    from app.pipeline.agent import Layer1Outcome

    records = [
        {
            "query_id": "q1",
            "registry_id": "HRB 6684",
            "registry_court": "Amtsgericht München",
            "name_normalized_register_name": "Siemens Aktiengesellschaft",
            "jurisdiction_confirmed": "DE",
            "confidence": 0.95,
            "source": "https://www.handelsregister.de",
            "no_match_reason": None,
            "provider": "handelsregister",
            "snippet": None,
        }
    ]

    async def dead_agent(query, mcps, run_id):
        return Layer1Outcome(candidates=[], records=records, errors=["boom: 529"])

    monkeypatch.setattr(runner, "run_layer1", dead_agent)
    query = QueryRow(query_id="q1", name="Siemens", jurisdiction="DE")
    result = await runner.process_query(query, run_id="test-salvage")

    assert not (result.no_match_reason or "").startswith("layer1_error")
    assert result.query_id == "q1"


async def test_error_row_still_returned_without_records(monkeypatch):
    from app.pipeline import runner
    from app.pipeline.agent import Layer1Outcome

    async def dead_agent(query, mcps, run_id):
        return Layer1Outcome(candidates=[], records=[], errors=["boom: 529"])

    monkeypatch.setattr(runner, "run_layer1", dead_agent)
    query = QueryRow(query_id="q2", name="Siemens", jurisdiction="DE")
    result = await runner.process_query(query, run_id="test-salvage")
    assert (result.no_match_reason or "").startswith("layer1_error")


async def test_rate_limited_get_spaces_requests_and_retries_429():
    import httpx

    from app.integrations import http as http_helpers

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http_helpers._throttles.pop("test-throttle", None)
    resp = await http_helpers.rate_limited_get(
        "test-throttle", client, "https://x.invalid/a", min_interval=0.01
    )
    assert resp.status_code == 200 and calls["n"] == 2  # 429 retried once

    # Spacing: two immediate requests must be >= min_interval apart.
    import time

    t0 = time.monotonic()
    await http_helpers.rate_limited_get(
        "test-throttle", client, "https://x.invalid/b", min_interval=0.05
    )
    await http_helpers.rate_limited_get(
        "test-throttle", client, "https://x.invalid/c", min_interval=0.05
    )
    assert time.monotonic() - t0 >= 0.05
    await client.aclose()


def test_match_without_id_gets_reason_and_loses_to_grounded_agent():
    from app.pipeline.enrichment import names_agree as _names_agree
    from app.pipeline.runner import _result_from_match

    match = {
        "decision": "match",
        "confidence": 1.0,
        "winning_candidate": {
            "registry_id": None,
            "registry_court": None,
            "name_normalized_register_name": "Tesco",
            "jurisdiction_confirmed": "GB",
            "source": "wikidata",
        },
    }
    row = _result_from_match(QueryRow(query_id="q", name="Tesco", jurisdiction="UK"), match)
    assert row.registry_id is None
    assert row.no_match_reason == "id_not_in_sources"  # blank ID always says why

    assert _names_agree("Tesco", "TESCO PLC")
    assert _names_agree("Heineken N.V.", "heineken n.v.")
    assert not _names_agree("Tesco", "Shopify Inc.")
    assert not _names_agree(None, "Tesco")


def test_matcher_jurisdiction_alias_and_empty_target():
    from app.matching.company_matcher import Target, score_record

    record = {
        "name_normalized_register_name": "TESCO PLC",
        "jurisdiction_confirmed": "GB",
        "confidence": 0.9,
    }
    # "UK" target must NOT penalize a GB record (alias, same country).
    uk = score_record(record, Target(name="Tesco", jurisdiction="UK"))
    gb = score_record(record, Target(name="Tesco", jurisdiction="GB"))
    assert uk.jurisdiction_match and gb.jurisdiction_match
    # An empty target constrains nothing — no blanket penalty.
    none = score_record(record, Target(name="Tesco", jurisdiction=""))
    assert none.jurisdiction_match
