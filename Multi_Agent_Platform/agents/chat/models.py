"""Pydantic contracts for the chat layer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agents.contracts import IncidentEvent


class ChatIntent(str, Enum):
    ASK = "ask"
    PROPOSE_EVENT = "propose_event"
    FOLLOW_INCIDENT = "follow_incident"
    CONFIRM = "confirm"
    CANCEL = "cancel"


class WarehouseRoleName(str, Enum):
    WAREHOUSE_WORKER = "warehouse_worker"
    WAREHOUSE_MANAGER = "warehouse_manager"


class ChatTurnRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    incident_id: str | None = None
    transaction_id: str | None = None
    tenant_id: str = "default"
    user_id: str = "operator"
    channel: str = "api"
    user_role: WarehouseRoleName = WarehouseRoleName.WAREHOUSE_WORKER
    confirm_proposal_id: str | None = None
    cancel_proposal_id: str | None = None
    wait_for_workflow: bool = False


class EventCard(BaseModel):
    kind: str = "event"
    proposal_id: str
    title: str
    summary: str
    event: IncidentEvent
    missing_fields: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class QueryCard(BaseModel):
    kind: str = "query"
    sql: str | None = None
    row_count: int = 0
    error: str | None = None
    preview: list[dict[str, Any]] = Field(default_factory=list)


class IncidentCard(BaseModel):
    kind: str = "incident"
    incident_id: str
    status: str
    event_type: str | None = None
    summary: str = ""
    agents: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class ChatTurnResponse(BaseModel):
    session_id: str
    intent: ChatIntent
    reply: str
    cards: list[dict[str, Any]] = Field(default_factory=list)
    incident_id: str | None = None
    transaction_id: str | None = None
    pending_proposal_id: str | None = None
    workflow: dict[str, Any] | None = None


class ClassifiedTurn(BaseModel):
    """Structured output from the cheap classifier (or the rule fallback)."""

    intent: ChatIntent = ChatIntent.ASK
    event_type: str | None = None
    order_id: int | None = None
    product_id: int | None = None
    reported_qty: int | None = None
    bin_id: str | None = None
    asn_id: str | None = None
    counted_qty: int | None = None
    rationale: str = ""
