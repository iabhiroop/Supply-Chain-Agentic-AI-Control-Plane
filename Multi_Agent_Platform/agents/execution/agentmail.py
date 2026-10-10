"""AgentMail client: exactly three org inboxes (buyer + vendor A + vendor C).

Email is the B2B wire. Company specialists never mail each other. VEND-B has
no inbox (three-inbox cap); carrier stays on EDI fixtures.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agents.config.settings import settings

logger = logging.getLogger(__name__)

# AgentMail Idempotency-Key: A-Z a-z 0-9 - . _ ~ only (400 otherwise).
_IDEM_DISALLOWED = re.compile(r"[^A-Za-z0-9._~-]+")


def _idempotency_key(key: str | None) -> str | None:
    if not key:
        return None
    cleaned = _IDEM_DISALLOWED.sub("-", str(key)).strip("-._~")
    return cleaned or None


BUYER_CLIENT_ID = "scm-buyer-v1"
VENDOR_SPECS = {
    "VEND-A": {
        "client_id": "scm-vend-a-v1",
        "username": "scm-vend-a",
        "display_name": "Asturias Dairy Coop",
        "party": "vendor",
    },
    "VEND-C": {
        "client_id": "scm-vend-c-v1",
        "username": "scm-vend-c",
        "display_name": "Premium Iberia",
        "party": "vendor",
    },
}

_inboxes: dict[str, dict[str, str]] = {}


def agentmail_live() -> bool:
    return bool(settings.agentmail_api_key.strip())


def agentmail_ready() -> bool:
    boxes = inbox_map()
    return bool(boxes.get("buyer") and boxes.get("VEND-A") and boxes.get("VEND-C"))


def client():
    if not agentmail_live():
        raise RuntimeError("AGENTMAIL_API_KEY is not set")
    from agentmail import AgentMail

    return AgentMail(api_key=settings.agentmail_api_key)


def inbox_map() -> dict[str, dict[str, str]]:
    return dict(_inboxes)


def _as_dict(inbox: Any) -> dict[str, str]:
    return {
        "inbox_id": str(getattr(inbox, "inbox_id", "")),
        "email": str(getattr(inbox, "email", "")),
        "client_id": str(getattr(inbox, "client_id", "") or ""),
        "display_name": str(getattr(inbox, "display_name", "") or ""),
    }


def _api_code(exc: Any) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return str(body.get("code") or "")
    return ""


def _find_listed(am: Any, client_id: str) -> dict[str, str] | None:
    from agentmail.core.api_error import ApiError

    try:
        listed = am.inboxes.list(limit=100)
    except ApiError as exc:
        if _api_code(exc) == "missing_permission":
            return None
        raise
    for inbox in listed.inboxes or []:
        if str(getattr(inbox, "client_id", "") or "") == client_id:
            return _as_dict(inbox)
    return None


def _create_or_get(am: Any, *, client_id: str, username: str, display_name: str, metadata: dict) -> dict[str, str]:
    from agentmail.core.api_error import ApiError
    from agentmail.errors.conflict_error import ConflictError
    from agentmail.inboxes.types.create_inbox_request import CreateInboxRequest

    found = _find_listed(am, client_id)
    if found:
        return found
    request = CreateInboxRequest(
        username=username,
        display_name=display_name,
        client_id=client_id,
        metadata=metadata,
    )
    try:
        inbox = am.inboxes.create(request=request)
        return _as_dict(inbox)
    except ConflictError:
        found = _find_listed(am, client_id)
        if found:
            return found
        raise
    except ApiError as exc:
        # Cap / idempotent reuse: never create a fourth; reuse if client_id already exists.
        if getattr(exc, "status_code", None) in {409, 422} or _api_code(exc) == "limit_exceeded":
            found = _find_listed(am, client_id)
            if found:
                return found
        raise


def _persist_vendor_email(vendor_id: str, email: str) -> None:
    if not email:
        return
    try:
        from agents.config.db import get_conn

        with get_conn() as conn:
            conn.execute(
                "UPDATE vendors SET email = %s WHERE vendor_id = %s",
                (email, vendor_id),
            )
    except Exception:  # noqa: BLE001
        logger.warning("Could not persist %s AgentMail address onto vendors.email", vendor_id)


def ensure_three_inboxes() -> dict[str, dict[str, str]]:
    """Idempotent: reuse client_id; never create a fourth mailbox."""
    global _inboxes
    if agentmail_ready():
        return dict(_inboxes)
    if not agentmail_live():
        return {}
    from agentmail.core.api_error import ApiError

    try:
        am = client()
        out: dict[str, dict[str, str]] = {}
        buyer = _create_or_get(
            am,
            client_id=BUYER_CLIENT_ID,
            username="scm-buyer",
            display_name="SCM Commercial Buyer",
            metadata={"party": "buyer", "role": "company_facade"},
        )
        if settings.agentmail_inbox_buyer:
            buyer["inbox_id"] = settings.agentmail_inbox_buyer
        out["buyer"] = buyer
        for vendor_id, spec in VENDOR_SPECS.items():
            out[vendor_id] = _create_or_get(
                am,
                client_id=spec["client_id"],
                username=spec["username"],
                display_name=spec["display_name"],
                metadata={"party": "vendor", "vendor_id": vendor_id},
            )
            _persist_vendor_email(vendor_id, out[vendor_id].get("email") or "")
        _inboxes = out
        logger.info(
            "AgentMail inboxes ready: buyer=%s vend-a=%s vend-c=%s",
            buyer.get("email"),
            out.get("VEND-A", {}).get("email"),
            out.get("VEND-C", {}).get("email"),
        )
        return out
    except ApiError as exc:
        code = None
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            code = body.get("code")
        logger.warning(
            "AgentMail inbox setup failed (%s %s). Need buyer + VEND-A + VEND-C "
            "(client_ids scm-buyer-v1, scm-vend-a-v1, scm-vend-c-v1). "
            "The three-inbox cap is full if another mailbox occupies a slot.",
            getattr(exc, "status_code", "?"),
            code or type(exc).__name__,
        )
        return {}
    except Exception:  # noqa: BLE001
        logger.warning("AgentMail inbox setup failed; EMAIL will use fixtures until inboxes exist.")
        return {}


def buyer_inbox_id() -> str:
    if not _inboxes:
        ensure_three_inboxes()
    return (_inboxes.get("buyer") or {}).get("inbox_id") or ""


def buyer_email() -> str:
    if not _inboxes:
        ensure_three_inboxes()
    return (_inboxes.get("buyer") or {}).get("email") or ""


def vendor_inbox(vendor_id: str) -> dict[str, str]:
    if not _inboxes:
        ensure_three_inboxes()
    return _inboxes.get(vendor_id) or {}


def send_from_buyer(*, to: str, subject: str, text: str, idempotency_key: str | None = None) -> dict[str, str]:
    am = client()
    inbox_id = buyer_inbox_id()
    resp = am.inboxes.messages.send(
        inbox_id,
        to=[to],
        subject=subject,
        text=text,
        idempotency_key=_idempotency_key(idempotency_key),
    )
    return {"message_id": str(resp.message_id), "thread_id": str(resp.thread_id)}


def send_from_vendor(
    vendor_id: str,
    *,
    to: str,
    subject: str,
    text: str,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    am = client()
    box = vendor_inbox(vendor_id)
    inbox_id = box.get("inbox_id") or ""
    resp = am.inboxes.messages.send(
        inbox_id,
        to=[to],
        subject=subject,
        text=text,
        idempotency_key=_idempotency_key(idempotency_key),
    )
    return {"message_id": str(resp.message_id), "thread_id": str(resp.thread_id)}


def list_messages(inbox_id: str, *, limit: int = 25) -> list[Any]:
    am = client()
    resp = am.inboxes.messages.list(inbox_id, limit=limit)
    return list(resp.messages or [])


def get_message(inbox_id: str, message_id: str) -> Any:
    am = client()
    return am.inboxes.messages.get(inbox_id, message_id)


def _addr(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_addr(v) for v in value)
    return str(value)


def message_from(msg: Any) -> str:
    return _addr(getattr(msg, "from_", None) or getattr(msg, "from", "") or "")


def message_text(msg: Any) -> str:
    return str(
        getattr(msg, "text", None)
        or getattr(msg, "extracted_text", None)
        or getattr(msg, "preview", None)
        or ""
    )


def correlate_mail(subject: str, body: str) -> dict[str, str | None]:
    blob = f"{subject}\n{body}"
    rfq = re.search(r"(RFQ-\d+-VEND-[A-C])", blob, re.I)
    po = re.search(r"(PO-\d+)", blob, re.I)
    pr = re.search(r"(PR-\d+)", blob, re.I)
    vendor = re.search(r"(VEND-[A-C])", blob, re.I)
    tx_id = None
    vendor_id = vendor.group(1).upper() if vendor else None
    try:
        from agents.config.db import fetch_one

        if rfq:
            row = fetch_one(
                "SELECT transaction_id, vendor_id FROM rfqs WHERE rfq_id = %s",
                (rfq.group(1).upper(),),
            )
            if row:
                tx_id = row["transaction_id"]
                vendor_id = row.get("vendor_id") or vendor_id
        if not tx_id and po:
            row = fetch_one(
                "SELECT transaction_id, vendor_id FROM exec_purchase_orders WHERE po_id = %s",
                (po.group(1).upper(),),
            )
            if row:
                tx_id = row["transaction_id"]
                vendor_id = row.get("vendor_id") or vendor_id
        if not tx_id and pr:
            row = fetch_one(
                "SELECT transaction_id FROM purchase_requisitions WHERE pr_id = %s",
                (pr.group(1).upper(),),
            )
            if row:
                tx_id = row["transaction_id"]
    except Exception:  # noqa: BLE001
        pass
    return {
        "transaction_id": tx_id,
        "vendor_id": vendor_id,
        "rfq_id": rfq.group(1).upper() if rfq else None,
        "po_id": po.group(1).upper() if po else None,
    }


def parse_inbound_body(subject: str, body: str) -> dict[str, Any]:
    blob = f"{subject}\n{body}"
    quote = re.search(
        r"QUOTE\s+vendor_id=(\S+)\s+unit_price=([\d.]+)\s+quantity=(\d+)\s+lead_days=(\d+)",
        blob,
        re.I,
    )
    if quote:
        return {
            "type": "quote",
            "vendor_id": quote.group(1).upper(),
            "unit_price": float(quote.group(2)),
            "quantity": int(quote.group(3)),
            "lead_days": int(quote.group(4)),
            "channel": "EMAIL",
        }
    invoice = re.search(
        r"INVOICE\s+invoice_id=(\S+)\s+vendor_id=(\S+)\s+po_id=(\S+)\s+quantity=(\d+)\s+unit_price=([\d.]+)",
        blob,
        re.I,
    )
    if invoice:
        return {
            "type": "supplier_invoice",
            "invoice_id": invoice.group(1),
            "vendor_id": invoice.group(2).upper(),
            "po_id": invoice.group(3).upper(),
            "quantity": int(invoice.group(4)),
            "unit_price": float(invoice.group(5)),
        }
    delay = re.search(r"DELAY\s+po_id=(\S+)\s+slip_days=(\d+)", blob, re.I)
    if delay:
        return {"type": "delay", "po_id": delay.group(1).upper(), "slip_days": int(delay.group(2))}
    delayed = re.search(r"delayed\s+(\d+)\s+days", blob, re.I)
    if delayed:
        return {"type": "delay", "slip_days": int(delayed.group(1))}
    return {"type": "email"}
