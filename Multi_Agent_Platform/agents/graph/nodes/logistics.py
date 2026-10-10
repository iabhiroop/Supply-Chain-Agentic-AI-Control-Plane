"""Logistics agent — contracted rates, book, tracking, GR. Must not change PO price or post AP."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agents.config.db import get_conn
from agents.contracts import AgentResult, AgentStatus
from agents.execution import channels, policy, services, store
from agents.execution.loop import pack_turn
from agents.graph.state import LogisticsState
from agents.tools.knowledge_tools import similar_incident_context, similar_incident_rows

NAME = "Logistics_Agent"


def _delay_from(snap: dict) -> dict | None:
    for msg in snap["unread"]:
        parsed = channels.parse_delay(msg)
        if parsed:
            return {**parsed, "message_id": msg.get("message_id")}
    ctx = (snap["transaction"] or {}).get("context") or {}
    if ctx.get("rebooked"):
        return None
    if ctx.get("exception") == "supplier_delay" or ctx.get("slip_days"):
        return {"type": "delay", "slip_days": int(ctx.get("slip_days") or 0)}
    return None


async def logistics_node(state: LogisticsState, config: RunnableConfig):
    tx_id = state["transaction_id"]
    snap = store.get_snapshot(tx_id)
    delay = _delay_from(snap)
    findings = [
        f"PO {(snap['po'] or {}).get('po_id')} booking {(snap['booking'] or {}).get('booking_id')}",
        f"unread={len(snap['unread'])} delay={bool(delay)}",
    ]

    if delay:
        slip = int(delay.get("slip_days") or 0)
        ctx = (snap["transaction"] or {}).get("context") or {}
        vendor = (snap.get("po") or {}).get("vendor_id") or "VEND-A"
        past_rows = similar_incident_rows(vendor)
        options = policy.delay_recoveries(
            slip,
            snap["policy"],
            similar=past_rows,
            human_approved=bool(ctx.get("human_approved_slip")),
        )
        findings.append(f"slip_days={slip} legal_recoveries={options}")
        past = similar_incident_context(vendor)
        if past:
            findings.append(f"Prior delay playbook: {past[:400]}")
        try:
            from agents.memory.store import last_experience

            xp = last_experience(vendor_id=vendor)
            if xp:
                findings.append(
                    f"Prior experience [{xp.get('flow')}]: {(xp.get('summary') or '')[:240]}"
                )
        except Exception:  # noqa: BLE001
            pass
        if not options:
            result = AgentResult(
                status=AgentStatus.NEEDS_HUMAN,
                summary=f"Supplier delay of {slip} days exceeds slip policy; holding for a human.",
                findings=findings,
                confidence=0.9,
                verified=True,
                recommendation="Approve an exception or cancel the PO.",
            )
            with get_conn() as conn:
                store.set_stage(
                    conn,
                    tx_id,
                    "AWAITING_HUMAN",
                    status="AWAITING_HUMAN",
                    halt_reason=f"Delay of {slip} days exceeds slip policy",
                    context_patch={"slip_days": slip},
                    event="DELAY_HITL",
                )
            return pack_turn(NAME, result)

        rebooked = services.rebook_after_delay(tx_id, slip, agent_name=NAME)
        if rebooked.get("error"):
            result = AgentResult(
                status=AgentStatus.FAILED,
                summary=str(rebooked.get("detail") or rebooked),
                findings=findings,
                confidence=0.3,
                verified=False,
            )
            return pack_turn(NAME, result)
        if rebooked.get("pending_approval"):
            result = AgentResult(
                status=AgentStatus.NEEDS_HUMAN,
                summary="Delay exceeds policy; HITL required.",
                findings=findings,
                confidence=0.9,
                verified=True,
            )
            return pack_turn(NAME, result, tool_calls=1)

        result = AgentResult(
            status=AgentStatus.SUCCESS,
            summary=f"Rebooked {rebooked['booking_id']} within slip tolerance; new ETA {rebooked.get('eta')}.",
            findings=findings,
            actions_taken=[f"REBOOK {rebooked['booking_id']}"],
            evidence=[f"booking:{rebooked['booking_id']}"],
            confidence=0.92,
            verified=True,
        )
        return pack_turn(NAME, result, tool_calls=1)

    if not snap["po"]:
        result = AgentResult(
            status=AgentStatus.NOT_APPLICABLE,
            summary="No PO to move.",
            findings=findings,
            confidence=1.0,
            verified=True,
        )
        return pack_turn(NAME, result)

    booked = snap["booking"]
    if not booked:
        booked = services.book_shipment(tx_id, agent_name=NAME)
        if booked.get("error"):
            result = AgentResult(
                status=AgentStatus.FAILED,
                summary=str(booked.get("detail") or booked),
                findings=findings,
                confidence=0.3,
                verified=False,
            )
            return pack_turn(NAME, result)

    tracking = services.consume_tracking(tx_id, agent_name=NAME)
    delivered = bool(tracking.get("delivered"))
    findings.append(f"tracking events={tracking.get('events')} delivered={delivered}")

    if not delivered:
        result = AgentResult(
            status=AgentStatus.PARTIAL,
            summary="Shipment booked; waiting for a DELIVERED tracking event.",
            findings=findings,
            actions_taken=[f"BOOK {booked.get('booking_id')}"],
            confidence=0.7,
            verified=False,
            recommendation="Wait for carrier EDI 214.",
        )
        return pack_turn(NAME, result, tool_calls=1)

    gr = snap["gr"] or services.post_gr(tx_id, agent_name=NAME)
    if gr.get("error"):
        result = AgentResult(
            status=AgentStatus.PARTIAL,
            summary=str(gr.get("detail") or gr),
            findings=findings,
            confidence=0.4,
            verified=False,
        )
        return pack_turn(NAME, result)

    with get_conn() as conn:
        store.write_lane_memory(conn, "Spain", booked.get("carrier_name") or "Speedy Express", 5, tx_id)

    result = AgentResult(
        status=AgentStatus.SUCCESS,
        summary=f"Delivered and GR {gr['gr_id']} posted for qty {gr['quantity']}.",
        findings=findings,
        actions_taken=[f"BOOK {booked.get('booking_id')}", f"POST_GR {gr['gr_id']}"],
        evidence=[f"gr:{gr['gr_id']}", f"delivered:{delivered}"],
        confidence=0.95,
        verified=True,
        recommendation="Finance should capture invoices and match.",
    )
    return pack_turn(NAME, result, tool_calls=3)
