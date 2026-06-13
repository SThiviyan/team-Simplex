"""Tests for best-effort US state-registry scraping (MCP scrape entries)."""

import asyncio

import app.pipeline.agent as agent
from app.pipeline.mcp_registry import get_mcp_servers
from app.pipeline.models import ExtractionPayload, McpServerEntry, QueryRow


def _payload(name: str, confidence: float) -> ExtractionPayload:
    return ExtractionPayload(
        registry_id="C-1",
        registry_court=None,
        name_normalized_register_name=name,
        jurisdiction_confirmed="US",
        confidence=confidence,
        source="https://example.test/entity",
        no_match_reason=None,
    )


# --- model / registry ---------------------------------------------------------

def test_entry_kind_default_and_domain():
    e = McpServerEntry(rank=1, name="fl", url="https://search.sunbiz.org/Inquiry")
    assert e.kind == "mcp"  # default
    assert e.domain == "search.sunbiz.org"
    s = McpServerEntry(rank=1, name="fl", url="https://search.sunbiz.org/X", kind="scrape")
    assert s.kind == "scrape"
    assert not s.is_placeholder  # a real site, not example.invalid


def test_us_list_is_all_real_scrape_sites():
    us = get_mcp_servers("US")
    assert len(us) >= 50  # the 50 states + DC
    assert us[0].name == "delaware-sos"  # most-common incorporation state first
    assert all(e.kind == "scrape" for e in us)
    assert all(not e.is_placeholder for e in us)  # real URLs
    assert all(e.domain for e in us)  # each scoped to a real host
    assert len({e.rank for e in us}) == len(us)  # unique ranks
    assert [e.rank for e in us] == sorted(e.rank for e in us)


def test_via_label_describes_scrape():
    us = get_mcp_servers("US")
    label = agent._via_label(us[0])
    assert "scrape" in label.lower()
    assert us[0].url in label


# --- best-effort walk ---------------------------------------------------------

async def test_walk_gives_up_and_continues_past_failures(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(agent.settings, "pipeline_mock", False)
    states = get_mcp_servers("US")[:3]
    calls: list[str] = []

    async def fake_attempt(client, query, *, mcp):
        calls.append(mcp.name)
        if mcp.name == states[0].name:
            raise RuntimeError("state site is down")  # error -> give up, continue
        if mcp.name == states[1].name:
            return None  # company not registered here -> continue
        return _payload(query.name, 0.5)  # low-confidence hit (no early exit)

    monkeypatch.setattr(agent, "_attempt", fake_attempt)
    results = await agent.run_layer1(QueryRow(query_id="q1", name="Acme Inc", jurisdiction="US"), states)

    assert calls == [s.name for s in states]  # walked all three despite the failures
    assert len(results) == 1 and results[0].registry_id == "C-1"


async def test_walk_stops_early_on_confident_state(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(agent.settings, "pipeline_mock", False)
    states = get_mcp_servers("US")[:5]
    calls: list[str] = []

    async def fake_attempt(client, query, *, mcp):
        calls.append(mcp.name)
        return _payload(query.name, 0.95 if mcp.name == states[1].name else 0.3)

    monkeypatch.setattr(agent, "_attempt", fake_attempt)
    results = await agent.run_layer1(QueryRow(query_id="q1", name="X", jurisdiction="US"), states)

    # Stopped after the confident state — the remaining states were not scraped.
    assert calls == [states[0].name, states[1].name]
    assert any(r.confidence >= 0.95 for r in results)


async def test_slow_attempt_times_out_and_continues(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(agent.settings, "pipeline_mock", False)
    monkeypatch.setattr(agent.settings, "pipeline_attempt_timeout", 0.02)
    states = get_mcp_servers("US")[:2]
    calls: list[str] = []

    async def slow(client, query, *, mcp):
        calls.append(mcp.name)
        await asyncio.sleep(5)  # far exceeds the 0.02s per-attempt timeout
        return _payload(query.name, 0.95)

    monkeypatch.setattr(agent, "_attempt", slow)
    results = await agent.run_layer1(QueryRow(query_id="q1", name="X", jurisdiction="US"), states)

    # Each state was attempted but abandoned on timeout, so the walk continued and
    # ultimately reports a layer-1 error rather than hanging.
    assert calls == [s.name for s in states]
    assert len(results) == 1
    assert results[0].no_match_reason.startswith(agent.LAYER1_ERROR_PREFIX)
    assert "TimeoutError" in results[0].no_match_reason


async def test_all_states_failing_yields_layer1_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(agent.settings, "pipeline_mock", False)
    states = get_mcp_servers("US")[:3]

    async def fake_attempt(client, query, *, mcp):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent, "_attempt", fake_attempt)
    results = await agent.run_layer1(QueryRow(query_id="q1", name="X", jurisdiction="US"), states)

    assert len(results) == 1
    assert results[0].no_match_reason.startswith(agent.LAYER1_ERROR_PREFIX)
