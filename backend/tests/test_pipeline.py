import csv

import pytest

from app.config import settings
from app.pipeline.csv_io import RESULT_COLUMNS, read_queries, read_queries_text
from app.pipeline.filtering import prefilter
from app.pipeline.mcp_registry import get_mcp_servers
from app.pipeline.models import ExtractionResult, QueryRow
from app.pipeline.runner import run_pipeline


@pytest.fixture
def mock_mode():
    original = settings.pipeline_mock
    settings.pipeline_mock = True
    yield
    settings.pipeline_mock = original


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


def test_mcp_registry_country_mapping():
    de = get_mcp_servers("de")
    assert de and de[0].name == "handelsregister-mcp"
    assert [e.rank for e in de] == sorted(e.rank for e in de)

    assert get_mcp_servers("UK") == get_mcp_servers("GB")
    # Unmapped countries fall into the extra_eu bucket.
    assert get_mcp_servers("ZA")[0].name == "gleif-mcp"
    # US has its own dedicated state-registry scrape list (not the extra_eu bucket).
    us = get_mcp_servers("US")
    assert us and us[0].name == "delaware-sos"
    assert [e.rank for e in us] == sorted(e.rank for e in us)
    assert all(e.kind == "scrape" for e in us)
    assert get_mcp_servers("ZA") != us


def test_placeholder_detection():
    assert all(e.is_placeholder for e in get_mcp_servers("DE"))


def test_prefilter_dedupes_and_sorts():
    results = [
        ExtractionResult(query_id="q", registry_id="HRB 1", confidence=0.5),
        ExtractionResult(query_id="q", registry_id="HRB 1", confidence=0.9),
        ExtractionResult(query_id="q", confidence=0.3),  # no data, no reason -> dropped
        ExtractionResult(query_id="q", confidence=0.0, no_match_reason="not found"),
    ]
    filtered = prefilter(results)
    assert [r.confidence for r in filtered] == [0.9, 0.0]
    assert filtered[0].registry_id == "HRB 1"


async def test_pipeline_mock_end_to_end(mock_mode, tmp_path):
    summary = await run_pipeline(limit=2, output_dir=tmp_path)

    assert summary.rows_processed == 2
    assert len(summary.results) == 2
    assert all(r.confidence > 0 for r in summary.results)

    # Output is a clean comma-delimited CSV: header + one row per query, no comments.
    with open(summary.output_csv, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert not any(line.startswith("#") for line in lines)
    rows = list(csv.DictReader(lines))
    assert list(rows[0].keys()) == RESULT_COLUMNS
    assert len(rows) == 2
    assert rows[0]["query_id"] == "Q-001"


async def test_pipeline_preserves_input_order_and_isolates_crashes(mock_mode, tmp_path, monkeypatch):
    from app.pipeline import runner

    real_process = runner.process_query

    async def flaky(query):
        if query.query_id == "p002":
            raise RuntimeError("boom on row 2")
        return await real_process(query)

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


def test_run_csv_upload_rejects_garbage(mock_mode):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        resp = client.post("/api/pipeline/run-csv", files={"file": ("x.csv", b"", "text/csv")})
    assert resp.status_code == 400


async def test_layer1_surfaces_attempt_errors(monkeypatch):
    """If every Layer-1 attempt raises, the error must surface in no_match_reason
    instead of masquerading as 'no candidates' / 'no match'."""
    from app.pipeline import agent, evaluator

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # client init only; no call happens

    async def overloaded(client, query, *, mcp):
        raise RuntimeError("simulated 529 overloaded_error")

    monkeypatch.setattr(agent, "_attempt", overloaded)

    query = QueryRow(query_id="err-1", name="Tesco", jurisdiction="UK")
    candidates = await agent.run_layer1(query, get_mcp_servers("UK"))

    assert len(candidates) == 1
    assert candidates[0].no_match_reason.startswith("layer1_error: ")
    assert "simulated 529" in candidates[0].no_match_reason
    assert candidates[0].confidence == 0.0

    # The evaluator passes the infra error through untouched (no API call).
    final = await evaluator.evaluate(query, prefilter(candidates))
    assert final == candidates[0]


async def test_pipeline_single_adhoc_query(mock_mode, tmp_path):
    query = QueryRow(query_id="adhoc-1", name="Siemens AG", jurisdiction="DE")
    summary = await run_pipeline(queries=[query], output_dir=tmp_path)
    assert summary.rows_processed == 1
    assert summary.results[0].query_id == "adhoc-1"
