"""Tests for the confidence floor + one-shot recursive retry."""

import app.matching.recursion as rec
from app.matching.semantic_filter import _assemble_result


# --- confidence floor in the semantic layer -----------------------------------

def _match_input(confidence: float, idx: int = 0) -> dict:
    return {
        "decision": "match",
        "winning_candidate_index": idx,
        "confidence": confidence,
        "reasoning": "best candidate",
        "suggested_query": "",
    }


def test_low_confidence_match_becomes_recursive_with_expansion():
    cands = [{"name_normalized_register_name": "ACME GmbH", "confidence": 0.5}]
    res = _assemble_result(
        _match_input(0.5), cands, query_name="ACME GmbH", jurisdiction="DE"
    )
    assert res["decision"] == "recursive_search"
    assert res["recursive_search"]["suggested_query"] == "ACME Gesellschaft mit beschränkter Haftung"
    assert res["recursive_search"]["link_confidence"] >= 0.7
    assert res["winning_candidate"] is None


def test_low_confidence_match_without_expansion_becomes_no_match():
    cands = [{"name_normalized_register_name": "Acme Systems", "confidence": 0.5}]
    res = _assemble_result(
        _match_input(0.5), cands, query_name="Acme Systems", jurisdiction="US"
    )
    assert res["decision"] == "no_match"
    assert res["winning_candidate"] is None


def test_high_confidence_match_is_returned():
    cands = [{"name_normalized_register_name": "ACME GmbH", "confidence": 0.95}]
    res = _assemble_result(
        _match_input(0.95), cands, query_name="ACME GmbH", jurisdiction="DE"
    )
    assert res["decision"] == "match"
    assert res["winning_candidate"] is not None


# --- one-shot recursive retry orchestration -----------------------------------

def _recursive_winner(link_confidence: float = 0.9, suggested: str = "Bayerische Motoren Werke AG"):
    return {
        "query_id": "q1",
        "name": "BMW",
        "jurisdiction": "DE",
        "decision": "recursive_search",
        "confidence": link_confidence,
        "winning_candidate": None,
        "recursive_search": {"suggested_query": suggested, "link_confidence": link_confidence},
    }


async def test_retry_accepts_high_confidence_match(monkeypatch):
    async def fake(providers, name, juris, *, limit, model, mock):
        return {
            "decision": "match",
            "winning_candidate": {"name_normalized_register_name": name},
            "confidence": 0.95,
            "references": [],
            "candidates": [],
        }

    monkeypatch.setattr(rec, "_gather_and_match", fake)
    out = await rec.apply_recursive_retry([], [_recursive_winner()], mock=False)
    w = out[0]
    assert w["decision"] == "match"
    assert w["confidence"] == 0.95
    assert w["retried_query"] == "Bayerische Motoren Werke AG"
    assert w["winning_candidate"]["name_normalized_register_name"] == "Bayerische Motoren Werke AG"
    assert w["name"] == "BMW"  # still tied to the original query


async def test_retry_rejecting_low_confidence_result_is_no_match(monkeypatch):
    async def fake(*a, **k):
        return {"decision": "match", "winning_candidate": {"x": 1}, "confidence": 0.55,
                "references": [], "candidates": []}

    monkeypatch.setattr(rec, "_gather_and_match", fake)
    out = await rec.apply_recursive_retry([], [_recursive_winner()], mock=False)
    assert out[0]["decision"] == "no_match"
    assert out[0]["winning_candidate"] is None


async def test_low_link_confidence_skips_the_database(monkeypatch):
    calls = {"n": 0}

    async def fake(*a, **k):
        calls["n"] += 1
        return {"decision": "match", "confidence": 0.99}

    monkeypatch.setattr(rec, "_gather_and_match", fake)
    out = await rec.apply_recursive_retry([], [_recursive_winner(link_confidence=0.4)], mock=False)
    assert out[0]["decision"] == "no_match"
    assert calls["n"] == 0  # never re-queried — link confidence too low


async def test_leftover_low_confidence_match_is_dropped():
    winner = {"query_id": "q1", "name": "X", "jurisdiction": "DE", "decision": "match",
              "confidence": 0.5, "winning_candidate": {"x": 1}}
    out = await rec.apply_recursive_retry([], [winner], mock=False)
    assert out[0]["decision"] == "no_match"
    assert out[0]["winning_candidate"] is None


async def test_high_confidence_match_passes_through():
    winner = {"query_id": "q1", "name": "X", "jurisdiction": "DE", "decision": "match",
              "confidence": 0.95, "winning_candidate": {"x": 1}}
    out = await rec.apply_recursive_retry([], [winner], mock=False)
    assert out[0]["decision"] == "match"
    assert out[0]["winning_candidate"] == {"x": 1}


async def test_mock_mode_returns_winners_untouched():
    winner = _recursive_winner()
    out = await rec.apply_recursive_retry([], [winner], mock=True)
    assert out == [winner]
