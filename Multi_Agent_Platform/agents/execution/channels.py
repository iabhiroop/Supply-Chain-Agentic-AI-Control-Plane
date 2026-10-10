"""Partner channels: persist to channel_messages; EMAIL goes out via buyer inbox."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Json

from agents.config.settings import settings

logger = logging.getLogger(__name__)

FIX_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "database",
    "fixtures",
)

BID_FIXTURES = {
    "VEND-A": {"file": "bid_vend_a.email.txt", "unit_price": 10.20, "lead_days": 5, "quantity": 20},
    "VEND-B": {"file": "bid_vend_b.email.txt", "unit_price": 10.50, "lead_days": 8, "quantity": 20},
    "VEND-C": {"file": "bid_vend_c.email.txt", "unit_price": 11.20, "lead_days": 4, "quantity": 20},
}


def _read_fixture(name: str) -> str:
    path = os.path.join(FIX_DIR, name)
    if not os.path.exists(path):
        return ""
    return open(path, encoding="utf-8").read()


def insert_message(
    conn: psycopg.Connection,
    *,
    transaction_id: str,
    direction: str,
    channel: str,
    counterparty: str | None,
    subject: str,
    body: str,
    parsed: dict | None = None,
) -> str:
    message_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO channel_messages (
            message_id, transaction_id, direction, channel, counterparty, subject, body, parsed
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (message_id, transaction_id, direction, channel, counterparty, subject, body, Json(parsed)),
    )
    return message_id


def mark_read(conn: psycopg.Connection, message_id: str) -> None:
    conn.execute(
        "UPDATE channel_messages SET read_at = now() WHERE message_id = %s AND read_at IS NULL",
        (message_id,),
    )


def mark_all_unread_read(conn: psycopg.Connection, transaction_id: str) -> None:
    conn.execute(
        """
        UPDATE channel_messages SET read_at = now()
         WHERE transaction_id = %s AND read_at IS NULL AND direction = 'IN'
        """,
        (transaction_id,),
    )


def send_outbound(
    conn: psycopg.Connection,
    *,
    transaction_id: str,
    channel: str,
    counterparty: str,
    subject: str,
    body: str,
    parsed: dict | None = None,
    inject_reply: bool = True,
) -> dict[str, Any]:
    """Log an outbound message; EMAIL is sent from the company buyer inbox only."""
    out_id = insert_message(
        conn,
        transaction_id=transaction_id,
        direction="OUT",
        channel=channel,
        counterparty=counterparty,
        subject=subject,
        body=body,
        parsed=parsed,
    )
    if channel == "EMAIL":
        _send_company_email(conn, counterparty, subject, body, parsed)
    replies: list[str] = []
    kind = (parsed or {}).get("type")
    from agents.execution.agentmail import agentmail_ready

    live_email = (not settings.email_fixture_replies) and agentmail_ready()
    if inject_reply and kind == "rfq" and counterparty in BID_FIXTURES:
        if not live_email or counterparty == "VEND-B":
            replies.append(_inject_bid(conn, transaction_id, counterparty))
    if inject_reply and kind == "booking":
        replies.extend(_inject_tracking(conn, transaction_id, counterparty))
    if inject_reply and kind == "invoice_request":
        if not live_email:
            replies.append(_inject_supplier_invoice(conn, transaction_id, counterparty))
        replies.append(_inject_freight_invoice(conn, transaction_id))
    return {"message_id": out_id, "injected": replies}


def _send_company_email(conn, counterparty: str, subject: str, body: str, parsed: dict | None) -> None:
    from agents.execution import agentmail as mail

    if not mail.agentmail_live():
        return
    mail.ensure_three_inboxes()
    if not mail.agentmail_ready() or counterparty == "VEND-B":
        return
    try:
        row = conn.execute("SELECT email FROM vendors WHERE vendor_id = %s", (counterparty,)).fetchone()
        to_addr = (row or {}).get("email") or (mail.vendor_inbox(counterparty).get("email") if counterparty in mail.VENDOR_SPECS else "")
        if not to_addr or to_addr.endswith(".example"):
            logger.warning("No AgentMail address for %s; EMAIL not sent", counterparty)
            return
        key = None
        if parsed:
            key = parsed.get("rfq_id") or parsed.get("po_id") or parsed.get("type")
        mail.send_from_buyer(
            to=to_addr,
            subject=subject,
            text=body,
            idempotency_key=f"buyer-{counterparty}-{key}" if key else None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Buyer AgentMail send failed for %s", counterparty)


def _inject_bid(conn: psycopg.Connection, transaction_id: str, vendor_id: str) -> str:
    spec = BID_FIXTURES[vendor_id]
    body = _read_fixture(spec["file"]) or (
        f"{vendor_id} quotes {spec['quantity']} at {spec['unit_price']} lead {spec['lead_days']} days."
    )
    parsed = {
        "type": "quote",
        "vendor_id": vendor_id,
        "unit_price": spec["unit_price"],
        "quantity": spec["quantity"],
        "lead_days": spec["lead_days"],
        "channel": "EMAIL",
    }
    return insert_message(
        conn,
        transaction_id=transaction_id,
        direction="IN",
        channel="EMAIL",
        counterparty=vendor_id,
        subject=f"Quotation {vendor_id}",
        body=body,
        parsed=parsed,
    )


def _inject_tracking(conn: psycopg.Connection, transaction_id: str, counterparty: str) -> list[str]:
    raw = _read_fixture("edi214_golden.json")
    try:
        payload = json.loads(raw) if raw else {"events": []}
    except json.JSONDecodeError:
        payload = {"events": []}
    events = payload.get("events") or [
        {"event_code": "PICKUP", "detail": "Trailer hooked"},
        {"event_code": "IN_TRANSIT", "detail": "Departed origin"},
        {"event_code": "DELIVERED", "detail": "POD signed"},
    ]
    ids = []
    for event in events:
        ids.append(
            insert_message(
                conn,
                transaction_id=transaction_id,
                direction="IN",
                channel="EDI",
                counterparty=counterparty,
                subject=f"EDI 214 {event.get('event_code')}",
                body=json.dumps(event),
                parsed={"type": "tracking", **event},
            )
        )
    return ids


def _inject_supplier_invoice(conn: psycopg.Connection, transaction_id: str, counterparty: str) -> str:
    supplier_body = _read_fixture("invoice_vend_a.txt")
    return insert_message(
        conn,
        transaction_id=transaction_id,
        direction="IN",
        channel="PDF",
        counterparty=counterparty,
        subject="Supplier invoice",
        body=supplier_body,
        parsed={
            "type": "supplier_invoice",
            "invoice_id": "INV-A-1001",
            "vendor_id": counterparty,
            "quantity": 20,
            "unit_price": 10.20,
        },
    )


def _inject_freight_invoice(conn: psycopg.Connection, transaction_id: str) -> str:
    freight_body = _read_fixture("freight_invoice.txt")
    return insert_message(
        conn,
        transaction_id=transaction_id,
        direction="IN",
        channel="PDF",
        counterparty="Speedy Express",
        subject="Freight invoice",
        body=freight_body,
        parsed={
            "type": "freight_invoice",
            "freight_invoice_id": "FR-1001",
            "amount": 85.00,
        },
    )


def parse_delay(message: dict) -> dict | None:
    parsed = message.get("parsed") or {}
    if parsed.get("type") == "delay":
        return parsed
    body = message.get("body") or ""
    match = re.search(r"delayed\s+(\d+)\s+days", body, re.I)
    if match:
        return {"type": "delay", "slip_days": int(match.group(1))}
    return None
