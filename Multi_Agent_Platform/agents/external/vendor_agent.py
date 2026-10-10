"""Standalone supplier agents. They never write ERP PO / GR / match.

LLM calls go through the AI Gateway (`get_llm`), same as company specialists.
"""

from __future__ import annotations

import argparse
import logging
import re
import threading
import time
from typing import Literal

from pydantic import BaseModel, Field

from agents.config.settings import get_llm, settings
from agents.execution import agentmail as mail

logger = logging.getLogger(__name__)

POLICY = {
    "VEND-A": {
        "unit_price": 10.20,
        "min_price": 10.10,
        "lead_days": 5,
        "quantity": 20,
        "can_invoice": True,
        "can_counter": True,
    },
    "VEND-C": {
        "unit_price": 11.20,
        "min_price": 11.20,
        "lead_days": 4,
        "quantity": 20,
        "can_invoice": False,
        "can_counter": False,
    },
}

_processed: dict[str, set[str]] = {"VEND-A": set(), "VEND-C": set()}
_countered: set[str] = set()
_tick_lock = threading.Lock()


class VendorInbound(BaseModel):
    kind: Literal["rfq", "invoice_request", "revise", "po_notice", "other"] = Field(
        description="What the buyer is asking this supplier to do."
    )
    rfq_id: str | None = None
    po_id: str | None = None
    target_price: float | None = None


def _regex_classify(subject: str, body: str) -> VendorInbound:
    blob = f"{subject}\n{body}".lower()
    rfq = re.search(r"(rfq-\d+-vend-[a-c])", blob, re.I)
    po = re.search(r"(po-\d+)", blob, re.I)
    price = re.search(r"(?:target|revise|at)\s+\$?([\d.]+)", blob, re.I)
    if "revise" in blob or "please revise" in blob:
        return VendorInbound(
            kind="revise",
            rfq_id=rfq.group(1).upper() if rfq else None,
            target_price=float(price.group(1)) if price else None,
        )
    if "invoice" in blob:
        return VendorInbound(kind="invoice_request", po_id=po.group(1).upper() if po else None)
    if rfq or "please quote" in blob or blob.startswith("rfq"):
        return VendorInbound(kind="rfq", rfq_id=rfq.group(1).upper() if rfq else None)
    if po and ("purchase order" in blob or "po issued" in blob or "booking" in blob):
        return VendorInbound(kind="po_notice", po_id=po.group(1).upper() if po else None)
    return VendorInbound(kind="other")


def classify_inbound(subject: str, body: str) -> VendorInbound:
    fallback = _regex_classify(subject, body)
    try:
        llm = get_llm(tier="cheap", temperature=0.0)
        structured = llm.with_structured_output(VendorInbound, method="function_calling")
        result = structured.invoke(
            "Classify this buyer email for a supplier agent. "
            "Kinds: rfq, invoice_request, revise, po_notice, other.\n"
            f"Subject: {subject}\n\n{body[:1500]}"
        )
        if isinstance(result, VendorInbound):
            if not result.rfq_id:
                result.rfq_id = fallback.rfq_id
            if not result.po_id:
                result.po_id = fallback.po_id
            if result.kind == "other" and fallback.kind != "other":
                return fallback
            return result
    except Exception:  # noqa: BLE001
        logger.debug("Vendor classify fell back to regex (gateway unavailable or parse error)")
    return fallback


def _quote_body(vendor_id: str, price: float, rfq_id: str | None) -> str:
    spec = POLICY[vendor_id]
    rfq_line = rfq_id or ""
    return (
        f"{vendor_id} quotation for MAT-11.\n"
        f"QUOTE vendor_id={vendor_id} unit_price={price:.2f} "
        f"quantity={spec['quantity']} lead_days={spec['lead_days']}\n"
        f"{rfq_line}\n"
    )


def _invoice_body(vendor_id: str, po_id: str | None) -> str:
    spec = POLICY[vendor_id]
    po = po_id or "PO-1001"
    invoice_id = f"INV-A-{po.split('-')[-1]}"
    return (
        f"Supplier invoice for {po}.\n"
        f"INVOICE invoice_id={invoice_id} vendor_id={vendor_id} po_id={po} "
        f"quantity={spec['quantity']} unit_price={spec['unit_price']:.2f}\n"
    )


def _is_self(vendor_id: str, from_addr: str) -> bool:
    own = (mail.vendor_inbox(vendor_id).get("email") or "").lower()
    return bool(own) and own in (from_addr or "").lower()


def process_once(vendor_id: str) -> int:
    """Poll this vendor inbox and reply. Returns number of replies sent."""
    if vendor_id not in POLICY or not mail.agentmail_live():
        return 0
    mail.ensure_three_inboxes()
    if not mail.agentmail_ready():
        return 0
    box = mail.vendor_inbox(vendor_id)
    inbox_id = box.get("inbox_id") or ""
    if not inbox_id:
        return 0
    with _tick_lock:
        return _process_once_locked(vendor_id, inbox_id)


def _process_once_locked(vendor_id: str, inbox_id: str) -> int:
    buyer = mail.buyer_email()
    spec = POLICY[vendor_id]
    sent = 0
    seen = _processed.setdefault(vendor_id, set())
    for item in mail.list_messages(inbox_id, limit=20):
        mid = str(getattr(item, "message_id", ""))
        if not mid or mid in seen:
            continue
        full = mail.get_message(inbox_id, mid)
        from_addr = mail.message_from(full)
        if _is_self(vendor_id, from_addr):
            seen.add(mid)
            continue
        subject = str(getattr(full, "subject", "") or "")
        body = mail.message_text(full)
        classified = classify_inbound(subject, body)
        reply_subject = None
        reply_body = None
        if classified.kind == "rfq":
            reply_subject = f"Quotation {vendor_id} {classified.rfq_id or ''}".strip()
            reply_body = _quote_body(vendor_id, spec["unit_price"], classified.rfq_id)
        elif classified.kind == "revise" and spec["can_counter"] and vendor_id not in _countered:
            _countered.add(vendor_id)
            price = max(spec["min_price"], spec["unit_price"] - 0.10)
            reply_subject = f"Revised quotation {vendor_id} {classified.rfq_id or ''}".strip()
            reply_body = _quote_body(vendor_id, price, classified.rfq_id)
        elif classified.kind == "invoice_request" and spec["can_invoice"]:
            reply_subject = f"Invoice {classified.po_id or ''} {vendor_id}".strip()
            reply_body = _invoice_body(vendor_id, classified.po_id)
        if not reply_body or not buyer:
            seen.add(mid)
            continue
        mail.send_from_vendor(
            vendor_id,
            to=buyer,
            subject=reply_subject or f"{vendor_id} reply",
            text=reply_body,
            idempotency_key=f"{vendor_id}-{mid}-reply",
        )
        seen.add(mid)
        sent += 1
    return sent


def tick_vendors() -> int:
    return process_once("VEND-A") + process_once("VEND-C")


def run_forever(vendor_id: str) -> None:
    logging.basicConfig(level=logging.INFO)
    mail.ensure_three_inboxes()
    logger.info("Vendor agent %s polling inbox %s via gateway %s", vendor_id, mail.vendor_inbox(vendor_id).get("email"), settings.gateway_url)
    while True:
        try:
            process_once(vendor_id)
        except Exception:  # noqa: BLE001
            logger.exception("vendor_agent %s tick failed", vendor_id)
        time.sleep(max(2, settings.agentmail_poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone supplier AgentMail agent")
    parser.add_argument("--vendor", choices=["VEND-A", "VEND-C"], required=True)
    args = parser.parse_args()
    run_forever(args.vendor)


if __name__ == "__main__":
    main()
