"""Tests for owner enrichment (mock mode — no network)."""

from app.matching.owner_lookup import find_owner
from app.matching.pipeline import match_payload

_PAYLOAD = {
    "queries": [{"query_id": "q1", "name": "Sinpex", "jurisdiction": "DE"}],
    "results": [
        {
            "query_id": "q1",
            "name_normalized_register_name": "Sinpex GmbH",
            "jurisdiction_confirmed": "DE",
            "confidence": 0.95,
        }
    ],
}


def test_find_owner_mock_returns_record():
    owner = find_owner("Sinpex GmbH", "DE", registry_id="HRB 1", mock=True)
    assert owner is not None
    assert owner["owner_name"]
    assert set(owner) == {"owner_name", "owner_type", "ownership_basis", "confidence", "source"}


def test_find_owner_empty_name_is_none():
    assert find_owner("", "DE", mock=True) is None


async def test_match_payload_attaches_owner_to_match():
    winners = await match_payload(_PAYLOAD, mock=True)
    w = winners[0]
    assert w["decision"] == "match"
    assert w["owner"] is not None and w["owner"]["owner_name"]


async def test_owner_lookup_can_be_disabled():
    winners = await match_payload(_PAYLOAD, mock=True, owner_lookup=False)
    assert winners[0]["owner"] is None
