"""Procurement agent — PR completeness, approval policy, create PO. Must not book freight or match invoices."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agents.config.db import get_conn
from agents.contracts import AgentResult, AgentStatus
from agents.execution import policy, services, store
from agents.execution.loop import pack_turn
from agents.graph.state import LogisticsState

NAME = "Procurement_Agent"


async def procurement_node(state: LogisticsState, config: RunnableConfig):
    tx_id = state["transaction_id"]
    snap = store.get_snapshot(tx_id)
    pr = snap["pr"]
    quotes = snap["quotes"]
    complete, missing = policy.pr_is_complete(pr)
    findings = [f"PR complete={complete} missing={missing}", f"{len(quotes)} quotes"]

    if snap["po"]:
        result = AgentResult(
            status=AgentStatus.SUCCESS,
            summary=f"PO {snap['po']['po_id']} already issued.",
            findings=findings,
            actions_taken=[f"PO {snap['po']['po_id']}"],
            confidence=1.0,
            verified=True,
        )
        return pack_turn(NAME, result)

    if not complete:
        result = AgentResult(
            status=AgentStatus.FAILED,
            summary=f"PR incomplete: {missing}",
            findings=findings,
            confidence=0.3,
            verified=False,
        )
        return pack_turn(NAME, result)

    last_price = (snap["material"] or {}).get("last_price")
    max_pct = (snap["policy"] or {}).get("price_deviation_pct")
    legal = policy.legal_quotes(quotes, last_price, max_pct)
    winner = policy.pick_winner(legal)
    ctx = (snap["transaction"] or {}).get("context") or {}
    if not winner and ctx.get("hitl_answer") == "proceed" and quotes:
        winner = policy.pick_winner(quotes) or quotes[0]
        findings.append("Human allowed awarding a non-policy quote.")
    if not winner:
        from agents.execution.hitl import create_hitl_request

        parked = create_hitl_request(
            question="No policy-legal quote to award. How should procurement proceed?",
            transaction_id=tx_id,
            workflow="p2p",
            agent_name=NAME,
            step="decide",
            options=[
                {"id": "proceed", "label": "Award the cheapest quote anyway (exception)"},
                {"id": "keep_waiting", "label": "Wait for a legal bid"},
                {"id": "cancel", "label": "Cancel the requisition"},
            ],
            recommended_option="keep_waiting",
            rationale=f"last_price={last_price} max_pct={max_pct}",
            confidence=0.4,
            evidence=findings + [f"last_price={last_price} max_pct={max_pct}"],
        )
        result = AgentResult(
            status=AgentStatus.NEEDS_HUMAN,
            summary=parked.get("question") or "No policy-legal quote; waiting on a human.",
            findings=findings + [f"last_price={last_price} max_pct={max_pct}"],
            confidence=0.4,
            verified=True,
            recommendation="Keep waiting or cancel; do not guess a winner.",
        )
        return pack_turn(NAME, result, tool_calls=1)

    amount = policy.po_amount(pr["quantity"], winner["unit_price"])
    findings.append(
        f"Winner {winner['vendor_id']} @ {winner['unit_price']} amount={amount} "
        f"(last {last_price}, deviation {policy.deviation_pct(winner['unit_price'], last_price):.2f}%)"
    )

    posted = services.create_po(
        tx_id,
        vendor_id=winner["vendor_id"],
        unit_price=float(winner["unit_price"]),
        agent_name=NAME,
    )
    if posted.get("error"):
        result = AgentResult(
            status=AgentStatus.FAILED,
            summary=str(posted.get("detail") or posted),
            findings=findings,
            confidence=0.3,
            verified=False,
        )
        return pack_turn(NAME, result)

    with get_conn() as conn:
        store.write_commercial_memory(
            conn,
            winner["vendor_id"],
            pr["material_id"],
            float(winner["unit_price"]),
            None,
            tx_id,
        )

    if posted.get("pending_approval"):
        result = AgentResult(
            status=AgentStatus.NEEDS_HUMAN,
            summary=f"PO amount {posted['amount']} requires approval before issue.",
            findings=findings,
            actions_taken=["held PO for HITL"],
            confidence=0.9,
            verified=True,
            recommendation="Approve on the dashboard to issue the PO.",
        )
        return pack_turn(NAME, result, tool_calls=1)

    result = AgentResult(
        status=AgentStatus.SUCCESS,
        summary=f"Issued {posted['po_id']} to {posted['vendor_id']} at {posted['unit_price']}.",
        findings=findings,
        actions_taken=[f"CREATE_PO {posted['po_id']}"],
        evidence=[f"po:{posted['po_id']}"],
        confidence=0.95,
        verified=True,
        recommendation="Logistics should book the contracted carrier.",
    )
    return pack_turn(NAME, result, tool_calls=1)
