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
    assert get_mcp_servers("UK")[-1].url == "internal:global"
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
    assert result.confidence == 0.93


async def test_pipeline_mock_end_to_end(mock_mode, tmp_path):
    summary = await run_pipeline(limit=2, output_dir=tmp_path)

    assert summary.rows_processed == 2
    assert summary.run_id
    assert all(r.confidence > 0 for r in summary.results)

    # Output is a clean comma-delimited CSV: header + one row per query, no comments.
    with open(summary.output_csv, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert not any(line.startswith("#") for line in lines)
    rows = list(csv.DictReader(lines))
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

    rows = list(csv.DictReader(resp.text.splitlines()))
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
