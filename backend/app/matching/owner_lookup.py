"""Owner enrichment — web search for who owns a company, once it's been matched.

Runs only AFTER the matching layer has determined a winning registry entry for a
query. Given that confirmed company, it asks Claude (with the web_search tool) to
find the company's owner / ultimate parent / controlling shareholder and returns
it as a structured record that the matching layer attaches to the winner.

Structured output is enforced via output_config, and the server-side web-search
tool loop is driven to completion (pause_turn continuations), mirroring
app.pipeline.agent. Set mock=True (PIPELINE_MOCK / no ANTHROPIC_API_KEY) to skip
the network call and return a deterministic stub.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-4-8"
# Server-side web-search loop can pause; bound the number of continuations.
MAX_PAUSE_TURN_CONTINUATIONS = 5
_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}


class OwnerInfo(BaseModel):
    """Structured owner result. All fields required-but-nullable + extra=forbid so
    the generated JSON schema is valid for the API's structured-output format."""

    model_config = ConfigDict(extra="forbid")

    owner_name: str | None = Field(
        description="The entity or person that ultimately owns/controls the company "
        "(parent company, controlling shareholder, or ultimate beneficial owner). "
        "Null if it cannot be determined."
    )
    owner_type: str | None = Field(
        description="One of: parent_company, individual, family, state, "
        "private_equity, publicly_traded, foundation, cooperative, other. Null if unknown."
    )
    ownership_basis: str | None = Field(
        description="Short basis for the ownership, e.g. '100% subsidiary of X', "
        "'majority shareholder (62%)', 'publicly listed, no controlling owner'. Null if unknown."
    )
    confidence: float = Field(
        description="How confident the owner is correct, in [0, 1]. Be honest, not optimistic."
    )
    source: str | None = Field(
        description="A citable URL supporting the owner. Null if none."
    )

    def clamped_confidence(self) -> float:
        return max(0.0, min(1.0, self.confidence))


_OWNER_FORMAT = {"type": "json_schema", "schema": OwnerInfo.model_json_schema()}

_OWNER_SYSTEM = """You are a corporate-ownership research assistant. Given a company \
that has already been confirmed in an official business register, determine who OWNS \
or controls it, and return the result as structured JSON.

Guidance:
- The owner is the entity/person that ultimately owns or controls the company: its
  parent company, majority/controlling shareholder, or ultimate beneficial owner.
- Prefer the ultimate parent / controlling owner over an intermediate holding when clear.
- owner_type must be one of: parent_company, individual, family, state, private_equity,
  publicly_traded, foundation, cooperative, other.
- For a widely-held listed company with no single controlling owner, set owner_type
  'publicly_traded' and owner_name something like 'Publicly traded (free float)'.
- Use the web_search tool and base the answer on cited, recent sources — do not answer
  from memory alone. Provide a source URL.
- Be honest about uncertainty: calibrate confidence in [0, 1]; set owner_name to null
  (low confidence) if you genuinely cannot find the owner.
- Respond with the JSON object only."""


def _user_prompt(name: str, jurisdiction: str, registry_id: str | None) -> str:
    reg = f"\nRegistry ID: {registry_id}" if registry_id else ""
    return (
        "Find the OWNER of this confirmed company and return it as structured JSON.\n"
        f"Company: {name}\n"
        f"Jurisdiction: {jurisdiction}{reg}"
    )


def _mock_owner(name: str) -> dict[str, Any]:
    return {
        "owner_name": f"{name} Holding (mock)",
        "owner_type": "parent_company",
        "ownership_basis": "Mock mode: no web search performed.",
        "confidence": 0.0,
        "source": "mock://owner",
    }


def _extract_owner(response: anthropic.types.Message) -> OwnerInfo:
    text = next(b.text for b in reversed(response.content) if b.type == "text")
    return OwnerInfo.model_validate_json(text)


def _to_dict(payload: OwnerInfo) -> dict[str, Any] | None:
    """Drop results with no owner found so the winner's `owner` field is either a
    populated record or null."""
    if not payload.owner_name:
        return None
    return {
        "owner_name": payload.owner_name,
        "owner_type": payload.owner_type,
        "ownership_basis": payload.ownership_basis,
        "confidence": round(payload.clamped_confidence(), 4),
        "source": payload.source,
    }


def find_owner(
    name: str,
    jurisdiction: str,
    *,
    registry_id: str | None = None,
    client: anthropic.Anthropic | None = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 8000,
    timeout: float = 90.0,
    mock: bool = False,
) -> dict[str, Any] | None:
    """Web-search the owner of a (already-matched) company. Returns an owner dict
    or None when it cannot be determined / the call fails. Never raises."""
    if not name:
        return None
    if mock:
        return _mock_owner(name)

    if client is None:
        client = anthropic.Anthropic()
    client = client.with_options(timeout=timeout)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _user_prompt(name, jurisdiction, registry_id)}
    ]

    try:
        for _ in range(MAX_PAUSE_TURN_CONTINUATIONS):
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=_OWNER_SYSTEM,
                messages=messages,
                tools=[_WEB_SEARCH_TOOL],
                output_config={"format": _OWNER_FORMAT},
            )
            if response.stop_reason == "pause_turn":
                # Server-side web-search loop paused — append the turn and resume.
                messages = messages[:1] + [{"role": "assistant", "content": response.content}]
                continue
            if response.stop_reason == "refusal":
                logger.warning("owner lookup refused for %s", name)
                return None
            return _to_dict(_extract_owner(response))
    except Exception as exc:
        # Enrichment must never break the chain — log and move on.
        logger.warning("owner lookup failed for %s: %s", name, exc)
        return None

    logger.warning("owner lookup exceeded pause_turn budget for %s", name)
    return None
