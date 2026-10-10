"""Typed hand-off contracts between the orchestrator and its sub-agents.

plan.md §3.3 requires structured hand-offs rather than prompt-stuffed free
text, so the orchestrator can arbitrate, escalate on low confidence, and record
evidence.

Every field carries a default. Models do not reliably populate every field of a
schema even when it is marked required, and a hand-off that fails validation is
worse than one with a conservative default: we would lose the work the agent
already did. Missing confidence therefore degrades to 0.0, which the
orchestrator treats as "escalate", rather than raising.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    SUCCESS = "SUCCESS"              # charter fulfilled
    PARTIAL = "PARTIAL"              # some progress, more work needed
    FAILED = "FAILED"                # could not complete
    NEEDS_HUMAN = "NEEDS_HUMAN"      # blocked on a human decision
    NOT_APPLICABLE = "NOT_APPLICABLE"  # nothing for this agent to do


class AgentResult(BaseModel):
    """What every sub-agent returns to the orchestrator."""

    status: AgentStatus = Field(
        default=AgentStatus.PARTIAL,
        description="Outcome of this agent's work.",
    )
    summary: str = Field(
        default="",
        description="One or two sentences on what was done and what it means for the order.",
    )
    findings: list[str] = Field(
        default_factory=list,
        description="Facts established from the data, each traceable to a tool result.",
    )
    actions_taken: list[str] = Field(
        default_factory=list,
        description="Business mutations actually performed, e.g. 'adjusted bin A1-01 to 0'.",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="0-1 confidence in this result. Below 0.5 the orchestrator escalates.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Data the conclusion rests on, e.g. 'stock_ledger: RECEIPT +20 on 2026-08-22'.",
    )
    recommendation: str = Field(
        default="",
        description="What should happen next, if anything.",
    )
    verified: bool = Field(
        default=False,
        description="True when the agent confirmed an ack, tracking event, GR, or match.",
    )
    order_still_short: bool | None = Field(
        default=None,
        description="True if the order line still cannot be filled from warehouse stock.",
    )
    replenished: bool | None = Field(
        default=None,
        description="True if stock was transferred onto the pick face.",
    )
    customer_notify: bool | None = Field(
        default=None,
        description="True if the customer promise is still at risk and they should be notified.",
    )

    def is_confident(self, threshold: float = 0.5) -> bool:
        return self.confidence >= threshold

    def needs_escalation(self, threshold: float = 0.5) -> bool:
        return (
            self.status in (AgentStatus.NEEDS_HUMAN, AgentStatus.FAILED)
            or not self.is_confident(threshold)
        )

    def brief(self) -> str:
        """Compact rendering for the orchestrator's context window."""
        lines = [f"status={self.status.value} confidence={self.confidence:.2f}"]
        if self.summary:
            lines.append(self.summary)
        if self.findings:
            lines.append("findings: " + "; ".join(self.findings[:4]))
        if self.actions_taken:
            lines.append("actions: " + "; ".join(self.actions_taken[:4]))
        if self.recommendation:
            lines.append("recommendation: " + self.recommendation)
        return "\n".join(lines)


class BudgetExceeded(Exception):
    """Raised when an agent or workflow exceeds its bounded-autonomy caps."""


class IncidentEvent(BaseModel):
    """Inbound event that opens an incident (plan.md §4.2)."""

    event_type: str = Field(description="e.g. STOCK_MISMATCH, SHIPMENT_DELAYED")
    order_id: int | None = None
    product_id: int | None = None
    reported_qty: int | None = None
    bin_id: str | None = None
    asn_id: str | None = None
    counted_qty: int | None = None
    severity: str = "MEDIUM"
    tenant_id: str = "default"
    source: str = "manual"

    def idempotency_key(self) -> str:
        """Stable key so a redelivered event cannot open a second incident."""
        parts = [
            self.tenant_id, self.event_type, str(self.order_id),
            str(self.product_id), str(self.reported_qty), str(self.asn_id),
            str(self.counted_qty if self.counted_qty is not None else ""),
        ]
        return ":".join(parts)

    def describe(self) -> str:
        bits = [f"Event {self.event_type}"]
        if self.order_id is not None:
            bits.append(f"order {self.order_id}")
        if self.product_id is not None:
            bits.append(f"product {self.product_id}")
        if self.reported_qty is not None:
            bits.append(f"physically counted quantity {self.reported_qty}")
        if self.bin_id:
            bits.append(f"bin {self.bin_id}")
        if self.asn_id:
            bits.append(f"ASN {self.asn_id}")
        if self.counted_qty is not None:
            bits.append(f"ASN counted quantity {self.counted_qty}")
        return ", ".join(bits) + "."


class HitlOption(BaseModel):
    """One constrained choice on a human-in-the-loop request."""

    id: str
    label: str


class HitlRequest(BaseModel):
    """Durable model-originated HITL request (agent is unsure on a step)."""

    request_id: str = ""
    transaction_id: str | None = None
    workflow: str = "p2p"
    kind: str = "model"
    agent_name: str = ""
    step: str = "decide"
    question: str = ""
    options: list[HitlOption] = Field(default_factory=list)
    recommended_option: str | None = None
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    status: str = "open"
    selected_option: str | None = None
    human_note: str | None = None


class TransactionStage(str, Enum):
    INTENT = "INTENT"
    SOURCING = "SOURCING"
    PO_PENDING_APPROVAL = "PO_PENDING_APPROVAL"
    PO_ISSUED = "PO_ISSUED"
    DISPATCH_READY = "DISPATCH_READY"
    IN_TRANSIT = "IN_TRANSIT"
    RECEIVED = "RECEIVED"
    INVOICE_CAPTURED = "INVOICE_CAPTURED"
    MATCHED = "MATCHED"
    SETTLED = "SETTLED"
    EXCEPTION = "EXCEPTION"
    AWAITING_HUMAN = "AWAITING_HUMAN"


class StageResult(BaseModel):
    """Outcome of one specialist turn on an execution transaction."""

    stage: str = ""
    next_stage: str = ""
    verified: bool = False
    documents: dict = Field(default_factory=dict)


class ExecutionTransaction(BaseModel):
    """Canonical object for the purchase-intent-to-settlement spine."""

    transaction_id: str
    pr_id: str | None = None
    stage: str = TransactionStage.INTENT.value
    status: str = "OPEN"
    context: dict = Field(default_factory=dict)
    halt_reason: str | None = None
    tenant_id: str = "default"


class ShortPlant(BaseModel):
    plant_id: str
    plant_name: str = ""
    days_of_cover: float
    horizon_days: int
    shortage_units: float
    daily_demand: float = 0.0


class ReallocateSpec(BaseModel):
    po_id: str
    qty: float
    pct_of_po: float
    po_quantity: float = 0.0


class AlternateSpec(BaseModel):
    vendor_id: str
    lead_days: int
    premium_pct: float
    cover_pct: float
    unit_price: float = 0.0
    weekly_capacity: float = 0.0


class DisruptionRecommendation(BaseModel):
    """Typed Wave 3 output. Numbers come from twin SQL via Python policy."""

    vendor_id: str
    material_id: str
    short_plants: list[ShortPlant] = Field(default_factory=list)
    affected_po_ids: list[str] = Field(default_factory=list)
    reallocate: ReallocateSpec | None = None
    alternate: AlternateSpec | None = None
    expedite_rfq: bool = False
    shortage_units: float = 0.0
    summary: str = ""
