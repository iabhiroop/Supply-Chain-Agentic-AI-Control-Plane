"""Delete the leftover spam mailbox, create VEND-C, seed B2B test emails.

Usage (from Multi_Agent_Platform/):
    python scripts/seed_agentmail_cases.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.execution import agentmail as mail  # noqa: E402

SPAM_INBOX_ID = "spam-3356@agentmail.to"

# Machine-readable trailers match parse_inbound_body / vendor_agent classifiers.
CASES = [
    {
        "from": "buyer",
        "to": "VEND-A",
        "subject": "RFQ RFQ-1001-VEND-A MAT-11 x20",
        "text": (
            "Please quote 20 MAT-11 to WH-FR-01 need-by PR-1001. "
            "RFQ RFQ-1001-VEND-A."
        ),
        "key": "seed-rfq-1001-a",
        "case": "PR-1001 RFQ to winner",
    },
    {
        "from": "buyer",
        "to": "VEND-C",
        "subject": "RFQ RFQ-1001-VEND-C MAT-11 x20",
        "text": (
            "Please quote 20 MAT-11 to WH-FR-01 need-by PR-1001. "
            "RFQ RFQ-1001-VEND-C."
        ),
        "key": "seed-rfq-1001-c",
        "case": "PR-1001 RFQ to rogue bidder",
    },
    {
        "from": "VEND-A",
        "to": "buyer",
        "subject": "Quotation VEND-A RFQ-1001-VEND-A",
        "text": (
            "We can supply 20 EA Queso Cabrales at 10.20 EUR, lead 5 days, FOB.\n"
            "QUOTE vendor_id=VEND-A unit_price=10.20 quantity=20 lead_days=5\n"
            "RFQ-1001-VEND-A PR-1001\n"
        ),
        "key": "seed-bid-1001-a",
        "case": "PR-1001 legal bid 10.20",
    },
    {
        "from": "VEND-C",
        "to": "buyer",
        "subject": "Quotation VEND-C RFQ-1001-VEND-C",
        "text": (
            "Premium quote: 20 EA at 11.20 EUR, lead 4 days.\n"
            "QUOTE vendor_id=VEND-C unit_price=11.20 quantity=20 lead_days=4\n"
            "RFQ-1001-VEND-C PR-1001\n"
        ),
        "key": "seed-bid-1001-c",
        "case": "PR-1001 +12% bid (policy reject)",
    },
    {
        "from": "buyer",
        "to": "VEND-A",
        "subject": "Please send invoice PO-1001",
        "text": "Invoice request for PO-1001. Please send invoice PO-1001.",
        "key": "seed-inv-req-1001",
        "case": "PR-1001 invoice request",
    },
    {
        "from": "VEND-A",
        "to": "buyer",
        "subject": "Invoice INV-A-1001 PO-1001 VEND-A",
        "text": (
            "Supplier invoice for PO-1001.\n"
            "INVOICE invoice_id=INV-A-1001 vendor_id=VEND-A po_id=PO-1001 "
            "quantity=20 unit_price=10.20\n"
            "PR-1001\n"
        ),
        "key": "seed-inv-1001-a",
        "case": "PR-1001 supplier invoice",
    },
    {
        "from": "VEND-A",
        "to": "buyer",
        "subject": "Delay on PO-1002",
        "text": (
            "PO-1002 pickup delayed 3 days due to dock congestion at origin. "
            "New ready date is +3 days. — Asturias Dairy Coop\n"
            "DELAY po_id=PO-1002 slip_days=3\n"
            "PR-1002\n"
        ),
        "key": "seed-delay-1002",
        "case": "PR-1002 3-day delay (inside slip)",
    },
]


def _free_spam_slot() -> None:
    am = mail.client()
    listed = am.inboxes.list(limit=50)
    for inbox in listed.inboxes or []:
        inbox_id = str(getattr(inbox, "inbox_id", "") or "")
        email = str(getattr(inbox, "email", "") or "")
        client_id = str(getattr(inbox, "client_id", "") or "")
        if client_id in {mail.BUYER_CLIENT_ID, *(s["client_id"] for s in mail.VENDOR_SPECS.values())}:
            continue
        if inbox_id == SPAM_INBOX_ID or email == SPAM_INBOX_ID or email.startswith("spam-"):
            am.inboxes.delete(inbox_id)
            print(f"deleted leftover inbox {email}")
            return
    print("no leftover spam inbox to delete")


def _send(case: dict) -> dict:
    dest = mail.buyer_email() if case["to"] == "buyer" else mail.vendor_inbox(case["to"]).get("email")
    if case["from"] == "buyer":
        return mail.send_from_buyer(
            to=dest or "",
            subject=case["subject"],
            text=case["text"],
            idempotency_key=case["key"],
        )
    return mail.send_from_vendor(
        case["from"],
        to=dest or "",
        subject=case["subject"],
        text=case["text"],
        idempotency_key=case["key"],
    )


def main() -> int:
    if not mail.agentmail_live():
        print("AGENTMAIL_API_KEY is not set")
        return 1
    _free_spam_slot()
    boxes = mail.ensure_three_inboxes()
    if not mail.agentmail_ready():
        print("could not create all three inboxes:", {k: v.get("email") for k, v in boxes.items()})
        return 1
    print("inboxes", {k: v.get("email") for k, v in boxes.items()})
    for case in CASES:
        sent = _send(case)
        print(f"  [{case['case']}] sent={bool(sent.get('message_id'))} {case['subject']}")
        time.sleep(0.4)
    print("seeded", len(CASES), "test emails")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
