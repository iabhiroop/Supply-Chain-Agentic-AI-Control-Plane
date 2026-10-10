"""Named-agent rpm and daily $ buckets for the Taiwan sitting.

Production traffic still uses the Layer 2 virtual key. X-Agent-Id selects a
second limiter/budget so Procurement_Agent and Disruption_Orchestrator show
distinct GET /v1/budget rows. Budget_Probe is a $0 bucket so 429 is visible
without burning the sitting.
"""

from __future__ import annotations

NAMED_AGENT_LIMITS: dict[str, dict[str, float | int]] = {
    "Procurement_Agent": {"rpm": 40, "tpm": 80_000, "daily_usd": 8.0},
    "Disruption_Orchestrator": {"rpm": 20, "tpm": 120_000, "daily_usd": 25.0},
    "Budget_Probe": {"rpm": 1, "tpm": 100, "daily_usd": 0.0},
}


def limits_for(agent_id: str | None) -> dict[str, float | int] | None:
    if not agent_id:
        return None
    return NAMED_AGENT_LIMITS.get(agent_id)


def scope_id(agent_id: str) -> str:
    return f"agent:{agent_id}"
