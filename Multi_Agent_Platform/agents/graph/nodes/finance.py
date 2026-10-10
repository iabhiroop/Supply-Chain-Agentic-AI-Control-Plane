"""Finance agent — invoice ingest and 4-way match. Must not create POs."""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from agents.contracts import AgentResult, AgentStatus
from agents.execution import services, store
from agents.execution.loop import pack_turn
from agents.graph.state import LogisticsState

NAME = "Finance_Agent"


async def finance_node(state: LogisticsState, config: RunnableConfig):
    tx_id = state["transaction_id"]
    snap = store.get_snapshot(tx_id)
    findings = [
        f"PO {(snap['po'] or {}).get('po_id')} GR {(snap['gr'] or {}).get('gr_id')}",
        f"supplier_inv={bool(snap['supplier_invoice'])} freight_inv={bool(snap['freight_invoice'])}",
    ]

    if snap.get("match") and snap["match"].get("status") == "POSTED":
        result = AgentResult(
            status=AgentStatus.SUCCESS,
            summary=f"Already matched {snap['match']['match_id']}.",
            findings=findings,
            confidence=1.0,
            verified=True,
        )
        return pack_turn(NAME, result)

    if not snap["gr"]:
        result = AgentResult(
            status=AgentStatus.PARTIAL,
            summary="Cannot match until goods receipt is posted.",
            findings=findings,
            confidence=0.7,
            verified=False,
            recommendation="Wait for Logistics to post GR.",
        )
        return pack_turn(NAME, result)

    captured = await asyncio.to_thread(services.capture_invoices, tx_id, agent_name=NAME)
    if captured.get("error"):
        result = AgentResult(
            status=AgentStatus.FAILED,
            summary=str(captured.get("detail") or captured),
            findings=findings,
            confidence=0.3,
            verified=False,
        )
        return pack_turn(NAME, result)

    snap = store.get_snapshot(tx_id)
    if not (snap["supplier_invoice"] and snap["freight_invoice"]):
        result = AgentResult(
            status=AgentStatus.PARTIAL,
            summary="Invoices requested; waiting for supplier and freight PDFs.",
            findings=findings,
            confidence=0.6,
            verified=False,
        )
        return pack_turn(NAME, result, tool_calls=1)

    matched = services.post_match(tx_id, agent_name=NAME)
    if matched.get("error"):
        result = AgentResult(
            status=AgentStatus.FAILED,
            summary=str(matched.get("detail") or matched),
            findings=findings,
            confidence=0.3,
            verified=False,
        )
        return pack_turn(NAME, result)

    if matched.get("pending_approval"):
        result = AgentResult(
            status=AgentStatus.NEEDS_HUMAN,
            summary=f"Match variance {matched.get('variance_pct')} exceeds policy.",
            findings=findings,
            actions_taken=["held match for HITL"],
            confidence=0.9,
            verified=True,
            recommendation="Approve the match break on the dashboard.",
        )
        return pack_turn(NAME, result, tool_calls=2)

    result = AgentResult(
        status=AgentStatus.SUCCESS,
        summary=f"Four-way match {matched['match_id']} posted; transaction settled.",
        findings=findings + [f"variance_pct={matched.get('variance_pct')}"],
        actions_taken=["CAPTURE_INVOICE", "CAPTURE_FREIGHT_INVOICE", f"POST_MATCH {matched['match_id']}"],
        evidence=[f"match:{matched['match_id']}"],
        confidence=0.96,
        verified=True,
    )
    return pack_turn(NAME, result, tool_calls=3)
