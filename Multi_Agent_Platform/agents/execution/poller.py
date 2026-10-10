"""Buyer-inbox poller: AgentMail → channel_messages (company Sense)."""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.config.db import get_conn
from agents.config.settings import settings
from agents.execution import agentmail as mail
from agents.execution import channels

logger = logging.getLogger(__name__)


def _already_ingested(conn, message_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM channel_messages WHERE parsed->>'agentmail_message_id' = %s LIMIT 1",
        (message_id,),
    ).fetchone()
    return bool(row)


def poll_buyer_inbox() -> int:
    """Ingest vendor mail into channel_messages. Returns new row count."""
    if not mail.agentmail_live():
        return 0
    mail.ensure_three_inboxes()
    if not mail.agentmail_ready():
        return 0
    inbox_id = mail.buyer_inbox_id()
    buyer = (mail.buyer_email() or "").lower()
    if not inbox_id:
        return 0
    ingested = 0
    with get_conn() as conn:
        for item in mail.list_messages(inbox_id, limit=30):
            mid = str(getattr(item, "message_id", ""))
            if not mid or _already_ingested(conn, mid):
                continue
            full = mail.get_message(inbox_id, mid)
            from_addr = mail.message_from(full)
            if buyer and buyer in from_addr.lower():
                continue
            subject = str(getattr(full, "subject", "") or "")
            body = mail.message_text(full)
            parsed = mail.parse_inbound_body(subject, body)
            corr = mail.correlate_mail(subject, body)
            tx_id = corr.get("transaction_id")
            if not tx_id:
                continue
            if parsed.get("type") in {None, "email"} and "quot" in subject.lower():
                parsed["type"] = "quote"
            vendor_id = parsed.get("vendor_id") or corr.get("vendor_id")
            if vendor_id:
                parsed["vendor_id"] = vendor_id
            parsed["agentmail_message_id"] = mid
            parsed["channel"] = "EMAIL"
            channels.insert_message(
                conn,
                transaction_id=str(tx_id),
                direction="IN",
                channel="EMAIL",
                counterparty=str(vendor_id) if vendor_id else from_addr,
                subject=subject,
                body=body,
                parsed=parsed,
            )
            ingested += 1
    return ingested


def wait_for_inbound(
    transaction_id: str,
    *,
    parsed_type: str,
    min_count: int = 1,
    timeout_seconds: int | None = None,
) -> int:
    """Tick vendor agents + poll buyer until enough inbound rows exist."""
    from agents.external.vendor_agent import tick_vendors

    deadline = time.time() + (timeout_seconds if timeout_seconds is not None else settings.partner_wait_seconds)
    while time.time() < deadline:
        try:
            tick_vendors()
        except Exception:  # noqa: BLE001
            logger.debug("vendor tick failed while waiting for %s", parsed_type)
        try:
            poll_buyer_inbox()
        except Exception:  # noqa: BLE001
            logger.debug("buyer poll failed while waiting for %s", parsed_type)
        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM channel_messages
                 WHERE transaction_id = %s AND direction = 'IN'
                   AND parsed->>'type' = %s
                """,
                (transaction_id, parsed_type),
            ).fetchone()
            n = int((row or {}).get("n") or 0)
        if n >= min_count:
            return n
        time.sleep(1.5)
    return 0


def run_b2b_tick() -> dict[str, Any]:
    from agents.external.vendor_agent import tick_vendors

    vendors = 0
    buyer = 0
    try:
        vendors = tick_vendors()
    except Exception:  # noqa: BLE001
        logger.exception("external vendor tick failed")
    try:
        buyer = poll_buyer_inbox()
    except Exception:  # noqa: BLE001
        logger.exception("buyer inbox poll failed")
    return {"vendor_replies": vendors, "ingested": buyer}
