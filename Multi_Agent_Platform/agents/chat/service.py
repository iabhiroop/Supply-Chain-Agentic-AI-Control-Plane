"""Chat turn handler: classify, ground, emit cards, confirm into the runtime."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from agents.chat import store
from agents.chat.classify import classify
from agents.chat.copilot import answer_with_sql
from agents.chat.lookup import gather_facts, incident_snapshot
from agents.chat.models import (
    ChatIntent,
    ChatTurnRequest,
    ChatTurnResponse,
    EventCard,
    IncidentCard,
    QueryCard,
)
from agents.chat.resolve import resolve_entities
from agents.contracts import IncidentEvent
from agents.runtime import execute_workflow, open_incident

SUPPORTED_WORKFLOWS = {
    "STOCK_MISMATCH",
    "SHIPMENT_DELAYED",
    "CARRIER_REJECTED_LOAD",
    "GOODS_RECEIPT_DISCREPANCY",
}


def _session(req: ChatTurnRequest) -> dict[str, Any]:
    if req.session_id:
        existing = store.get_session(req.session_id)
        if existing:
            if req.incident_id and not existing.get("incident_id"):
                store.touch_session(req.session_id, incident_id=req.incident_id)
                existing["incident_id"] = req.incident_id
            return existing
    return store.create_session(
        tenant_id=req.tenant_id,
        user_id=req.user_id,
        channel=req.channel,
        incident_id=req.incident_id,
    )


def _pending(session: dict[str, Any]) -> dict[str, Any] | None:
    raw = session.get("pending_proposal")
    if not raw:
        return None
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _merge_event_fields(classified, resolved: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": classified.event_type or "STOCK_MISMATCH",
        "order_id": resolved.get("order_id") or classified.order_id,
        "product_id": resolved.get("product_id") or classified.product_id,
        "reported_qty": (
            resolved["reported_qty"]
            if resolved.get("reported_qty") is not None
            else classified.reported_qty
        ),
        "bin_id": resolved.get("bin_id") or classified.bin_id,
        "asn_id": classified.asn_id,
        "counted_qty": classified.counted_qty,
    }


def _missing(fields: dict[str, Any], event_type: str) -> list[str]:
    needed = ["order_id"]
    if event_type == "STOCK_MISMATCH":
        needed += ["product_id", "reported_qty"]
    if event_type == "GOODS_RECEIPT_DISCREPANCY":
        needed = ["asn_id"]
        if fields.get("counted_qty") is None and fields.get("reported_qty") is None:
            needed.append("counted_qty")
    return [k for k in needed if fields.get(k) is None]


def _p2p_brief(transaction_id: str) -> tuple[str, dict | None]:
    try:
        from agents.execution import store
        snap = store.get_snapshot(transaction_id)
    except KeyError:
        return f"No execution job {transaction_id}.", None
    tx = snap.get("transaction") or {}
    pr = snap.get("pr") or {}
    po = snap.get("po") or {}
    book = snap.get("booking") or {}
    reply = (
        f"{tx.get('pr_id') or pr.get('pr_id')} is {tx.get('stage')}/{tx.get('status')}."
        + (f" {tx.get('halt_reason')}." if tx.get("halt_reason") else "")
        + (f" PO {po.get('po_id')} {po.get('vendor_id')}." if po else "")
        + (f" Booking {book.get('status')}." if book else "")
    )
    card = {
        "kind": "job",
        "transaction_id": transaction_id,
        "pr_id": tx.get("pr_id"),
        "stage": tx.get("stage"),
        "status": tx.get("status"),
        "halt_reason": tx.get("halt_reason"),
    }
    return reply, card
    snap = incident_snapshot(incident_id)
    if not snap:
        return f"No incident {incident_id} in the system of record.", None
    inc = snap["incident"]
    card = IncidentCard(
        incident_id=str(inc["incident_id"]),
        status=inc.get("status") or "UNKNOWN",
        event_type=inc.get("event_type"),
        summary=inc.get("resolution_summary") or "",
        agents=snap["agent_runs"],
        actions=snap["actions"],
    )
    reply = (
        f"Incident {card.incident_id} is {card.status}"
        + (f" ({card.event_type})" if card.event_type else "")
        + "."
    )
    if card.summary:
        reply += f" {card.summary}"
    if card.agents:
        bits = [f"{a.get('agent_name')}={a.get('status')}" for a in card.agents]
        reply += " Agents: " + ", ".join(bits) + "."
    return reply, card


async def _start_workflow(event: IncidentEvent, *, wait: bool) -> dict[str, Any]:
    incident_id, created = open_incident(event)
    if not created:
        return {"incident_id": incident_id, "created": False, "status": "DUPLICATE"}
    if wait:
        result = await execute_workflow(incident_id, event, verbose=False)
        result["created"] = True
        return result
    asyncio.create_task(execute_workflow(incident_id, event, verbose=False))
    return {
        "incident_id": incident_id,
        "created": True,
        "status": "IN_PROGRESS",
        "summary": "Workflow started in the background. Ask for status on this thread.",
    }


async def _confirm_p2p_job(session_id: str, transaction_id: str, intent: ChatIntent) -> ChatTurnResponse:
    from agents.execution import services as exec_services
    from agents.execution.hitl import open_request_for
    from agents.execution.runtime import execute_transaction

    req = open_request_for(transaction_id=transaction_id)
    if req and req.get("kind") == "model":
        option = req.get("recommended_option") or ((req.get("options") or [{}])[0].get("id"))
        decided = exec_services.apply_human_decision(transaction_id, "answer", option=option)
    else:
        decided = exec_services.apply_human_decision(transaction_id, "approve_exception")
    if decided.get("error"):
        reply = decided.get("detail") or "This job is not waiting on a human."
        brief, card = _p2p_brief(transaction_id)
        cards = [card] if card else []
        store.append_message(session_id, "assistant", reply, intent=intent.value, cards=cards)
        return ChatTurnResponse(
            session_id=session_id, intent=intent, reply=f"{reply} {brief}", cards=cards, transaction_id=transaction_id,
        )
    workflow = None
    if decided.get("resumed"):
        workflow = await execute_transaction(transaction_id)
    brief, card = _p2p_brief(transaction_id)
    reply = f"Confirmed {decided.get('action')} on this purchase job. {brief}"
    cards = [card] if card else []
    store.append_message(session_id, "assistant", reply, intent=intent.value, cards=cards)
    return ChatTurnResponse(
        session_id=session_id,
        intent=intent,
        reply=reply,
        cards=cards,
        transaction_id=transaction_id,
        workflow=workflow,
    )


async def handle_turn(req: ChatTurnRequest) -> ChatTurnResponse:
    session = _session(req)
    session_id = str(session["session_id"])
    pending = _pending(session)
    incident_id = req.incident_id or (str(session["incident_id"]) if session.get("incident_id") else None)

    store.append_message(session_id, "user", req.message)

    classified = classify(
        req.message,
        has_pending=bool(pending),
        has_incident=bool(incident_id),
    )

    tx_id = req.transaction_id
    if tx_id and classified.intent is ChatIntent.PROPOSE_EVENT:
        classified.intent = ChatIntent.ASK
    if tx_id and classified.intent is ChatIntent.FOLLOW_INCIDENT:
        classified.intent = ChatIntent.ASK
    if tx_id and not pending:
        from agents.chat.classify import CONFIRM_RE
        if CONFIRM_RE.match(req.message):
            classified.intent = ChatIntent.CONFIRM

    if req.confirm_proposal_id:
        classified.intent = ChatIntent.CONFIRM
    if req.cancel_proposal_id:
        classified.intent = ChatIntent.CANCEL

    if classified.intent is ChatIntent.CANCEL:
        store.touch_session(session_id, pending_proposal=None)
        reply = "Cancelled. No incident was opened and no business data was changed."
        store.append_message(session_id, "assistant", reply, intent=classified.intent.value)
        return ChatTurnResponse(session_id=session_id, intent=classified.intent, reply=reply)

    if classified.intent is ChatIntent.CONFIRM:
        if not pending and tx_id:
            return await _confirm_p2p_job(session_id, tx_id, classified.intent)
        if not pending:
            reply = "There is no pending action to confirm. Describe the disruption first."
            store.append_message(session_id, "assistant", reply, intent=classified.intent.value)
            return ChatTurnResponse(session_id=session_id, intent=classified.intent, reply=reply)
        if req.confirm_proposal_id and req.confirm_proposal_id != pending.get("proposal_id"):
            reply = "That confirmation card is stale. Ask me to propose the event again."
            store.append_message(session_id, "assistant", reply, intent=classified.intent.value)
            return ChatTurnResponse(session_id=session_id, intent=classified.intent, reply=reply)

        event = IncidentEvent.model_validate(pending["event"])
        event.source = "chat"
        event.tenant_id = req.tenant_id
        if event.event_type not in SUPPORTED_WORKFLOWS:
            reply = (
                f"{event.event_type} is recognised but not wired as a workflow yet. "
                "Supported: STOCK_MISMATCH, SHIPMENT_DELAYED, CARRIER_REJECTED_LOAD, "
                "GOODS_RECEIPT_DISCREPANCY."
            )
            store.append_message(session_id, "assistant", reply, intent=classified.intent.value)
            return ChatTurnResponse(
                session_id=session_id,
                intent=classified.intent,
                reply=reply,
                pending_proposal_id=pending.get("proposal_id"),
            )

        workflow = await _start_workflow(event, wait=req.wait_for_workflow)
        store.touch_session(
            session_id,
            incident_id=workflow["incident_id"],
            pending_proposal=None,
        )
        if workflow.get("status") == "DUPLICATE":
            reply = (
                f"This event was already handled as incident {workflow['incident_id']}. "
                "No additional mutations were applied."
            )
        else:
            reply = (
                f"Confirmed. Opened incident {workflow['incident_id']} "
                f"({workflow.get('status')}). "
                + (workflow.get("summary") or "")
            )
        snap_reply, card = _incident_card(workflow["incident_id"])
        cards = [card.model_dump()] if card else []
        store.append_message(
            session_id, "assistant", reply, intent=classified.intent.value, cards=cards
        )
        return ChatTurnResponse(
            session_id=session_id,
            intent=classified.intent,
            reply=reply + ((" " + snap_reply) if card and workflow.get("status") != "IN_PROGRESS" else ""),
            cards=cards,
            incident_id=workflow["incident_id"],
            workflow=workflow,
        )

    if classified.intent is ChatIntent.FOLLOW_INCIDENT:
        if not incident_id:
            reply = "This thread is not bound to an incident yet. Confirm an event card first, or pass incident_id."
            store.append_message(session_id, "assistant", reply, intent=classified.intent.value)
            return ChatTurnResponse(session_id=session_id, intent=classified.intent, reply=reply)
        reply, card = _incident_card(incident_id)
        cards = [card.model_dump()] if card else []
        store.append_message(session_id, "assistant", reply, intent=classified.intent.value, cards=cards)
        return ChatTurnResponse(
            session_id=session_id,
            intent=classified.intent,
            reply=reply,
            cards=cards,
            incident_id=incident_id,
        )

    resolved = resolve_entities(req.message)
    if classified.intent is ChatIntent.PROPOSE_EVENT:
        fields = _merge_event_fields(classified, resolved)
        event = IncidentEvent(
            event_type=fields["event_type"],
            order_id=fields["order_id"],
            product_id=fields["product_id"],
            reported_qty=fields["reported_qty"],
            bin_id=fields["bin_id"],
            asn_id=fields.get("asn_id"),
            counted_qty=fields.get("counted_qty"),
            tenant_id=req.tenant_id,
            source="chat",
        )
        missing = _missing(fields, event.event_type)
        evidence = list(resolved.get("evidence") or [])
        facts = gather_facts(order_id=event.order_id, product_id=event.product_id, incident_id=None)
        if facts.get("product") and facts["product"].get("bins"):
            evidence += [
                f"system stock {b['bin_id']}={b['quantity']}" for b in facts["product"]["bins"]
            ]

        if missing:
            reply = (
                "I can open a disruption incident, but I still need: "
                + ", ".join(missing)
                + ". "
                + ("; ".join(evidence) if evidence else "")
            )
            store.append_message(session_id, "assistant", reply, intent=classified.intent.value)
            return ChatTurnResponse(
                session_id=session_id,
                intent=classified.intent,
                reply=reply.strip(),
                cards=[],
            )

        proposal_id = str(uuid.uuid4())
        if event.event_type == "GOODS_RECEIPT_DISCREPANCY":
            summary = (
                f"This will run inbound receiving for ASN {event.asn_id} "
                f"(counted {event.counted_qty if event.counted_qty is not None else event.reported_qty}). "
                "A goods receipt and supplier message are posted only after you confirm. No carrier booking."
            )
        else:
            summary = (
                f"This will run the outbound disruption workflow for order {event.order_id}"
                + (f", product {event.product_id}" if event.product_id is not None else "")
                + (f", counted qty {event.reported_qty}" if event.reported_qty is not None else "")
                + ". Inventory, carrier and customer email change only after you confirm."
            )
        card = EventCard(
            proposal_id=proposal_id,
            title=f"Open {event.event_type} incident",
            summary=summary,
            event=event,
            missing_fields=[],
            evidence=evidence,
        )
        store.touch_session(
            session_id,
            pending_proposal={
                "proposal_id": proposal_id,
                "event": event.model_dump(mode="json"),
            },
        )
        reply = (
            card.summary
            + " Reply yes to confirm, or no to cancel. Nothing has been written yet."
        )
        cards = [card.model_dump(mode="json")]
        store.append_message(session_id, "assistant", reply, intent=classified.intent.value, cards=cards)
        return ChatTurnResponse(
            session_id=session_id,
            intent=classified.intent,
            reply=reply,
            cards=cards,
            pending_proposal_id=proposal_id,
        )

    result = answer_with_sql(req.message, req.user_role.value, transaction_id=tx_id)
    card = QueryCard(
        sql=result.get("sql"),
        row_count=int(result.get("row_count") or 0),
        error=result.get("error"),
        preview=(result.get("rows") or [])[:3],
    )
    cards = [card.model_dump(mode="json")]
    reply = result.get("reply") or "No answer."
    store.append_message(session_id, "assistant", reply, intent=ChatIntent.ASK.value, cards=cards)
    return ChatTurnResponse(
        session_id=session_id,
        intent=ChatIntent.ASK,
        reply=reply,
        cards=cards,
        incident_id=incident_id,
        transaction_id=tx_id,
    )
