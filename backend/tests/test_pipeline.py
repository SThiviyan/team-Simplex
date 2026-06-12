import csv

import pytest

from app.config import settings
from app.pipeline.csv_io import RESULT_COLUMNS, read_queries
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


def test_mcp_registry_country_mapping():
    de = get_mcp_servers("de")
    assert de and de[0].name == "handelsregister-mcp"
    assert [e.rank for e in de] == sorted(e.rank for e in de)

    assert get_mcp_servers("UK") == get_mcp_servers("GB")
    # Unknown countries fall into the extra_eu bucket
    assert get_mcp_servers("CH") == get_mcp_servers("US")
    assert get_mcp_servers("CH")[0].name == "gleif-mcp"


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

    with open(summary.output_csv, encoding="utf-8") as f:
        data_lines = (line for line in f if not line.startswith("#"))
        rows = list(csv.DictReader(data_lines, delimiter=";"))
    assert list(rows[0].keys()) == RESULT_COLUMNS
    assert len(rows) == 2
    assert rows[0]["query_id"] == "Q-001"


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
