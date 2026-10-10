"""LangChain tools wrapping execution ERP APIs (LLM fallback / allow-lists)."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.tools.client import api_get, api_post


class TxInput(BaseModel):
    transaction_id: str = Field(description="Execution transaction UUID")


@tool("get_execution_snapshot", args_schema=TxInput)
def get_execution_snapshot(transaction_id: str):
    """Read the ERP snapshot for a purchase-to-pay transaction (PR, quotes, PO, booking, GR, invoices)."""
    return api_get(f"/execution/transactions/{transaction_id}")


@tool("issue_rfq", args_schema=TxInput)
def issue_rfq(transaction_id: str):
    """Issue RFQs to active vendors and ingest fixture bids as quotes. Sourcing only."""
    return api_post("/execution/rfq", {"transaction_id": transaction_id, "agent_name": "Sourcing_Agent"})


class CreatePoInput(BaseModel):
    transaction_id: str
    vendor_id: str
    unit_price: float


@tool("create_po", args_schema=CreatePoInput)
def create_po(transaction_id: str, vendor_id: str, unit_price: float):
    """Create a purchase order in simulated ERP. Procurement only. May hold for approval."""
    return api_post("/execution/po", {
        "transaction_id": transaction_id,
        "vendor_id": vendor_id,
        "unit_price": unit_price,
        "agent_name": "Procurement_Agent",
    })


@tool("book_shipment", args_schema=TxInput)
def book_shipment(transaction_id: str):
    """Book the contracted carrier for the PO. Logistics only."""
    return api_post("/execution/booking", {"transaction_id": transaction_id, "agent_name": "Logistics_Agent"})


@tool("post_gr", args_schema=TxInput)
def post_gr(transaction_id: str):
    """Post goods receipt at counted quantity. Logistics only after DELIVERED."""
    return api_post("/execution/gr", {"transaction_id": transaction_id, "agent_name": "Logistics_Agent"})


@tool("capture_invoice", args_schema=TxInput)
def capture_invoice(transaction_id: str):
    """Capture supplier and freight invoices from the channel inbox. Finance only."""
    return api_post("/execution/invoices", {"transaction_id": transaction_id, "agent_name": "Finance_Agent"})


@tool("post_match", args_schema=TxInput)
def post_match(transaction_id: str):
    """Post a four-way match (PO, GR, supplier invoice, freight). Finance only."""
    return api_post("/execution/match", {"transaction_id": transaction_id, "agent_name": "Finance_Agent"})


class HitlRequestInput(BaseModel):
    question: str = Field(description="What the human must decide.")
    transaction_id: str | None = Field(default=None, description="P2P execution transaction UUID, if any.")
    agent_name: str = Field(default="", description="Specialist creating the request.")
    step: str = Field(default="decide", description="sense | decide | act | verify | recommend")
    options: list[dict] | None = Field(
        default=None,
        description="Constrained choices [{id, label}]. Defaults to proceed / keep_waiting / cancel.",
    )
    recommended_option: str | None = Field(default=None, description="Option id the agent would pick.")
    rationale: str = Field(default="", description="Why the agent is unsure.")
    confidence: float | None = Field(default=None, description="0-1 confidence in the recommended option.")
    workflow: str = Field(default="p2p", description="p2p or disruption")


@tool("request_human", args_schema=HitlRequestInput)
def request_human(
    question: str,
    transaction_id: str | None = None,
    agent_name: str = "",
    step: str = "decide",
    options: list[dict] | None = None,
    recommended_option: str | None = None,
    rationale: str = "",
    confidence: float | None = None,
    workflow: str = "p2p",
):
    """Park this step and ask a human to pick one constrained option. Use when you are not sure."""
    from agents.execution.hitl import create_hitl_request

    return create_hitl_request(
        question=question,
        transaction_id=transaction_id or None,
        workflow=workflow or "p2p",
        agent_name=agent_name or "",
        step=step or "decide",
        options=options,
        recommended_option=recommended_option,
        rationale=rationale or "",
        confidence=confidence,
    )
