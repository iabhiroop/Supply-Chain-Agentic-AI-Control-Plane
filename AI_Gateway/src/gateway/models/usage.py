"""
Usage & metering models.

UsageRecord is written for every request (including cache hits).
UsageSummary and CostBreakdown are used for analytics queries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class UsageRecord(BaseModel):
    """A single request's usage record, written to PostgreSQL (or SQLite in dev)."""
    id: UUID = Field(default_factory=uuid4)
    gateway_request_id: str = ""

    # Who
    key_id: UUID | None = None
    organization_id: str = "default"
    team_id: str = "default"
    project_id: str = "default"
    agent_id: str | None = None

    # What
    provider: str = ""
    model: str = ""
    endpoint: str = "/v1/chat/completions"

    # Tokens
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    # Cost
    cost_usd: float = 0.0

    # Performance
    latency_ms: int = 0
    
    # Routing
    route_strategy: str = "static"
    cache_hit: bool = False
    fallback_used: bool = False
    route_reason: str = ""
    error_code: str = ""
    tpm_remaining: int | None = None
    cache_cost_avoided_usd: float = 0.0

    # Context
    metadata: dict[str, Any] = Field(default_factory=dict)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UsageSummary(BaseModel):
    """Aggregated usage stats for a given scope and time range."""
    scope_type: str  # "organization", "team", "project", "key"
    scope_id: str
    period_start: datetime
    period_end: datetime

    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cached_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_cost_usd: float = 0.0
    cache_hit_rate: float = 0.0
    avg_latency_ms: float = 0.0


class CostBreakdown(BaseModel):
    """Cost broken down by a dimension (model, provider, team, etc.)."""
    dimension: str  # "model", "provider", "team", "project", "agent"
    period_start: datetime
    period_end: datetime
    items: list[CostBreakdownItem] = Field(default_factory=list)
    total_cost_usd: float = 0.0


class CostBreakdownItem(BaseModel):
    """A single item in a cost breakdown."""
    label: str  # e.g. "gemini-2.5-flash" or "team-logistics"
    request_count: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    pct_of_total: float = 0.0
