"""ERP document APIs used by agents and HTTP routers.

Every mutation records an inverse on `actions` and writes verified facts back
to the simulated ERP tables. Partners are reached only through channel adapters.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from agents.config.db import get_conn
from agents.config.settings import settings
from agents.contracts import TransactionStage
from agents.execution import channels, policy, store
from api.database.db import record_action


def _suffix(pr_id: str | None) -> str:
    if not pr_id:
        return "X"
    return pr_id.split("-")[-1]


def issue_rfqs(transaction_id: str, *, agent_name: str = "Sourcing_Agent") -> dict[str, Any]:
    """Qualify active vendors, send RFQs from the buyer inbox, persist quotes."""
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        pr = snap["pr"]
        if not pr:
            return {"error": True, "detail": "No purchase requisition on this transaction"}
        vendors = snap["vendors"]
        created = []
        for vendor in vendors:
            rfq_id = f"RFQ-{_suffix(pr['pr_id'])}-{vendor['vendor_id']}"
            existing = conn.execute("SELECT rfq_id FROM rfqs WHERE rfq_id = %s", (rfq_id,)).fetchone()
            if existing:
                created.append({"rfq_id": rfq_id, "vendor_id": vendor["vendor_id"], "status": "EXISTS"})
                continue
            conn.execute(
                """
                INSERT INTO rfqs (rfq_id, transaction_id, pr_id, vendor_id, status)
                VALUES (%s, %s, %s, %s, 'SENT')
                """,
                (rfq_id, transaction_id, pr["pr_id"], vendor["vendor_id"]),
            )
            channels.send_outbound(
                conn,
                transaction_id=transaction_id,
                channel="EMAIL",
                counterparty=vendor["vendor_id"],
                subject=f"RFQ {rfq_id} {pr['material_id']} x{pr['quantity']}",
                body=(
                    f"Please quote {pr['quantity']} {pr['material_id']} to {pr['plant_id']} "
                    f"need-by {pr['need_by']}. RFQ {rfq_id}."
                ),
                parsed={"type": "rfq", "rfq_id": rfq_id, "vendor_id": vendor["vendor_id"]},
                inject_reply=True,
            )
            record_action(
                conn,
                agent_name=agent_name,
                action_type="ISSUE_RFQ",
                transaction_id=transaction_id,
                payload={"rfq_id": rfq_id, "vendor_id": vendor["vendor_id"]},
                inverse={"endpoint": "/api/execution/rfq/cancel", "rfq_id": rfq_id},
            )
            created.append({"rfq_id": rfq_id, "vendor_id": vendor["vendor_id"], "status": "SENT"})

        quotes = _ingest_quotes(conn, transaction_id)
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.SOURCING.value,
            status="OPEN",
            context_patch={"rfqs": created, "quote_ids": [q["quote_id"] for q in quotes]},
            event="RFQS_ISSUED",
        )

    if len(quotes) < 2:
        from agents.execution.agentmail import agentmail_ready
        from agents.execution.poller import wait_for_inbound

        if agentmail_ready() and not settings.email_fixture_replies:
            wait_for_inbound(transaction_id, parsed_type="quote", min_count=2)
            with get_conn() as conn:
                quotes = _ingest_quotes(conn, transaction_id)
                store.set_stage(
                    conn,
                    transaction_id,
                    TransactionStage.SOURCING.value,
                    status="OPEN",
                    context_patch={"rfqs": created, "quote_ids": [q["quote_id"] for q in quotes]},
                    event="RFQS_ISSUED",
                )
    return {"rfqs": created, "quotes": quotes}


def _ingest_quotes(conn, transaction_id: str) -> list[dict]:
    pr = store._row(
        conn,
        "SELECT pr_id, quantity FROM purchase_requisitions WHERE transaction_id = %s LIMIT 1",
        (transaction_id,),
    )
    rfq_rows = store._rows(conn, "SELECT * FROM rfqs WHERE transaction_id = %s", (transaction_id,))
    rfq_by_vendor = {r["vendor_id"]: r["rfq_id"] for r in rfq_rows}
    unread = store._rows(
        conn,
        "SELECT * FROM channel_messages WHERE transaction_id = %s AND direction = 'IN' AND parsed->>'type' = 'quote'",
        (transaction_id,),
    )
    quotes = []
    for msg in unread:
        parsed = msg.get("parsed") or {}
        vendor_id = parsed.get("vendor_id") or msg.get("counterparty")
        quote_id = f"Q-{vendor_id}-{_suffix((pr or {}).get('pr_id'))}"
        exists = conn.execute("SELECT quote_id FROM quotes WHERE quote_id = %s", (quote_id,)).fetchone()
        if exists:
            quotes.append(store._row(conn, "SELECT * FROM quotes WHERE quote_id = %s", (quote_id,)))
            channels.mark_read(conn, msg["message_id"])
            continue
        rfq_id = rfq_by_vendor.get(vendor_id)
        conn.execute(
            """
            INSERT INTO quotes (quote_id, transaction_id, rfq_id, vendor_id, unit_price, quantity, lead_days, channel)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'EMAIL')
            """,
            (
                quote_id,
                transaction_id,
                rfq_id,
                vendor_id,
                parsed.get("unit_price"),
                parsed.get("quantity") or (pr or {}).get("quantity"),
                parsed.get("lead_days") or 7,
            ),
        )
        channels.mark_read(conn, msg["message_id"])
        quotes.append(store._row(conn, "SELECT * FROM quotes WHERE quote_id = %s", (quote_id,)))
    return [q for q in quotes if q]


def create_po(
    transaction_id: str,
    *,
    vendor_id: str,
    unit_price: float,
    agent_name: str = "Procurement_Agent",
    human_approved: bool = False,
) -> dict[str, Any]:
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        pr = snap["pr"]
        if not pr:
            return {"error": True, "detail": "PR missing"}
        existing = snap["po"]
        if existing:
            return existing
        amount = policy.po_amount(pr["quantity"], unit_price)
        complete, missing = policy.pr_is_complete(pr)
        if not complete:
            return {"error": True, "detail": f"PR incomplete: {missing}"}
        budget = (snap["cost_center"] or {}).get("budget_usd")
        if not policy.within_budget(amount, budget):
            return {"error": True, "detail": f"Amount {amount} exceeds cost-center budget {budget}"}
        last_price = (snap["material"] or {}).get("last_price")
        max_pct = (snap["policy"] or {}).get("price_deviation_pct")
        if not policy.quote_is_legal(unit_price, last_price, max_pct):
            return {"error": True, "detail": "Winning bid exceeds price deviation policy"}
        hitl = policy.needs_approval(amount, snap["policy"], snap["approval_rules"], pr["cost_center"])
        if hitl and not human_approved:
            store.set_stage(
                conn,
                transaction_id,
                TransactionStage.PO_PENDING_APPROVAL.value,
                status="AWAITING_HUMAN",
                halt_reason=f"PO amount {amount} requires approval",
                context_patch={
                    "pending_po": {
                        "vendor_id": vendor_id,
                        "unit_price": unit_price,
                        "amount": amount,
                    }
                },
                event="PO_PENDING_APPROVAL",
            )
            return {
                "pending_approval": True,
                "amount": amount,
                "vendor_id": vendor_id,
                "unit_price": unit_price,
            }

        po_id = f"PO-{_suffix(pr['pr_id'])}"
        conn.execute(
            """
            INSERT INTO exec_purchase_orders (
                po_id, transaction_id, vendor_id, material_id, quantity, unit_price,
                plant_id, cost_center, need_by, status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ISSUED')
            """,
            (
                po_id, transaction_id, vendor_id, pr["material_id"], pr["quantity"],
                unit_price, pr["plant_id"], pr["cost_center"], pr["need_by"],
            ),
        )
        conn.execute(
            "UPDATE purchase_requisitions SET status = 'ORDERED' WHERE pr_id = %s",
            (pr["pr_id"],),
        )
        record_action(
            conn,
            agent_name=agent_name,
            action_type="CREATE_PO",
            transaction_id=transaction_id,
            payload={"po_id": po_id, "vendor_id": vendor_id, "unit_price": unit_price, "quantity": pr["quantity"]},
            inverse={"endpoint": "/api/execution/po/cancel", "po_id": po_id},
        )
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.PO_ISSUED.value,
            status="OPEN",
            halt_reason=None,
            context_patch={"po_id": po_id, "vendor_id": vendor_id, "unit_price": unit_price, "amount": amount},
            event="PO_ISSUED",
        )
        return store._row(conn, "SELECT * FROM exec_purchase_orders WHERE po_id = %s", (po_id,))


def approve_pending_po(transaction_id: str) -> dict[str, Any]:
    snap = store.get_snapshot(transaction_id)
    pending = (snap["transaction"].get("context") or {}).get("pending_po") or {}
    if not pending:
        po = snap["po"]
        if po:
            return po
        return {"error": True, "detail": "No pending PO to approve"}
    return create_po(
        transaction_id,
        vendor_id=pending["vendor_id"],
        unit_price=pending["unit_price"],
        human_approved=True,
    )


def book_shipment(transaction_id: str, *, agent_name: str = "Logistics_Agent") -> dict[str, Any]:
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        po = snap["po"]
        if not po:
            return {"error": True, "detail": "No PO to book against"}
        if snap["booking"] and snap["booking"].get("status") != "CANCELLED":
            return snap["booking"]
        rate = snap["contracted_rate"]
        if not rate:
            return {"error": True, "detail": "No contracted rate for this plant"}
        booking_id = f"BK-{_suffix(snap['pr']['pr_id'] if snap['pr'] else po['po_id'])}"
        eta = date.today() + timedelta(days=5)
        conn.execute(
            """
            INSERT INTO bookings (
                booking_id, transaction_id, po_id, carrier_id, carrier_name,
                service_level, freight_cost, eta, status
            ) VALUES (%s,%s,%s,%s,%s,'STANDARD',%s,%s,'BOOKED')
            """,
            (
                booking_id, transaction_id, po["po_id"], rate["carrier_id"],
                rate["carrier_name"], rate["rate_usd"], eta,
            ),
        )
        channels.send_outbound(
            conn,
            transaction_id=transaction_id,
            channel="EDI",
            counterparty=rate["carrier_name"],
            subject=f"Booking {booking_id}",
            body=f"Book {booking_id} on {rate['carrier_name']} for {po['po_id']}",
            parsed={"type": "booking", "booking_id": booking_id},
            inject_reply=True,
        )
        record_action(
            conn,
            agent_name=agent_name,
            action_type="BOOK_SHIPMENT",
            transaction_id=transaction_id,
            payload={"booking_id": booking_id, "carrier_name": rate["carrier_name"], "freight_cost": rate["rate_usd"]},
            inverse={"endpoint": "/api/execution/booking/cancel", "booking_id": booking_id},
        )
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.DISPATCH_READY.value,
            status="OPEN",
            context_patch={"booking_id": booking_id, "eta": eta.isoformat()},
            event="BOOKED",
        )
        return store._row(conn, "SELECT * FROM bookings WHERE booking_id = %s", (booking_id,))


def consume_tracking(transaction_id: str, *, agent_name: str = "Logistics_Agent") -> dict[str, Any]:
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        booking = snap["booking"]
        if not booking:
            return {"error": True, "detail": "No booking"}
        unread = [
            m for m in store._rows(
                conn,
                "SELECT * FROM channel_messages WHERE transaction_id = %s AND direction = 'IN' AND parsed->>'type' = 'tracking' AND read_at IS NULL",
                (transaction_id,),
            )
        ]
        events = []
        for msg in unread:
            parsed = msg.get("parsed") or {}
            code = parsed.get("event_code") or "IN_TRANSIT"
            conn.execute(
                """
                INSERT INTO tracking_events (booking_id, transaction_id, event_code, detail)
                VALUES (%s, %s, %s, %s)
                """,
                (booking["booking_id"], transaction_id, code, parsed.get("detail")),
            )
            channels.mark_read(conn, msg["message_id"])
            events.append(code)
        delivered = any(
            e["event_code"] == "DELIVERED"
            for e in store._rows(
                conn,
                "SELECT event_code FROM tracking_events WHERE transaction_id = %s",
                (transaction_id,),
            )
        )
        stage = TransactionStage.IN_TRANSIT.value
        status = "OPEN"
        conn.execute(
            "UPDATE bookings SET status = %s WHERE booking_id = %s",
            ("DELIVERED" if delivered else "IN_TRANSIT", booking["booking_id"]),
        )
        if delivered:
            store.set_stage(conn, transaction_id, stage, status=status, event="IN_TRANSIT")
        else:
            store.set_stage(conn, transaction_id, stage, status=status, event="TRACKING")
        return {"events": events, "delivered": delivered, "booking_id": booking["booking_id"]}


def post_gr(transaction_id: str, *, quantity: int | None = None, agent_name: str = "Logistics_Agent") -> dict[str, Any]:
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        po = snap["po"]
        if not po:
            return {"error": True, "detail": "No PO"}
        if snap["gr"]:
            return snap["gr"]
        qty = int(quantity if quantity is not None else po["quantity"])
        gr_id = f"GR-{_suffix(po['po_id'])}"
        conn.execute(
            """
            INSERT INTO goods_receipts_exec (gr_id, transaction_id, po_id, quantity, plant_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (gr_id, transaction_id, po["po_id"], qty, po["plant_id"]),
        )
        record_action(
            conn,
            agent_name=agent_name,
            action_type="POST_GR",
            transaction_id=transaction_id,
            payload={"gr_id": gr_id, "quantity": qty, "po_id": po["po_id"]},
            inverse={"endpoint": "/api/execution/gr/cancel", "gr_id": gr_id},
        )
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.RECEIVED.value,
            status="OPEN",
            context_patch={"gr_id": gr_id, "gr_qty": qty},
            event="GR_POSTED",
        )
        return store._row(conn, "SELECT * FROM goods_receipts_exec WHERE gr_id = %s", (gr_id,))


def capture_invoices(transaction_id: str, *, agent_name: str = "Finance_Agent") -> dict[str, Any]:
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        po = snap["po"]
        if not po:
            return {"error": True, "detail": "No PO"}
        requested = False
        if not snap["supplier_invoice"]:
            vendor_id = po["vendor_id"]
            channels.send_outbound(
                conn,
                transaction_id=transaction_id,
                channel="EMAIL",
                counterparty=vendor_id,
                subject=f"Please send invoice {po['po_id']}",
                body=f"Invoice request for {po['po_id']}. Please send invoice {po['po_id']}.",
                parsed={"type": "invoice_request", "po_id": po["po_id"], "vendor_id": vendor_id},
                inject_reply=True,
            )
            requested = True

    if requested:
        from agents.execution.agentmail import agentmail_ready
        from agents.execution.poller import wait_for_inbound

        if agentmail_ready() and not settings.email_fixture_replies:
            wait_for_inbound(transaction_id, parsed_type="supplier_invoice", min_count=1)

    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        po = snap["po"]
        if not po:
            return {"error": True, "detail": "No PO"}
        unread = store._rows(
            conn,
            """
            SELECT * FROM channel_messages
             WHERE transaction_id = %s AND direction = 'IN'
               AND parsed->>'type' IN ('supplier_invoice','freight_invoice')
               AND read_at IS NULL
            """,
            (transaction_id,),
        )
        captured = {}
        for msg in unread:
            parsed = msg.get("parsed") or {}
            if parsed.get("type") == "supplier_invoice" and not store._row(
                conn, "SELECT invoice_id FROM supplier_invoices WHERE transaction_id = %s", (transaction_id,)
            ):
                invoice_id = parsed.get("invoice_id") or f"INV-{po['vendor_id']}-{_suffix(po['po_id'])}"
                conn.execute(
                    """
                    INSERT INTO supplier_invoices (
                        invoice_id, transaction_id, po_id, vendor_id, quantity, unit_price, status
                    ) VALUES (%s,%s,%s,%s,%s,%s,'CAPTURED')
                    """,
                    (
                        invoice_id, transaction_id, po["po_id"], parsed.get("vendor_id") or po["vendor_id"],
                        parsed.get("quantity") or po["quantity"], parsed.get("unit_price") or po["unit_price"],
                    ),
                )
                record_action(
                    conn,
                    agent_name=agent_name,
                    action_type="CAPTURE_INVOICE",
                    transaction_id=transaction_id,
                    payload={"invoice_id": invoice_id},
                    inverse={"endpoint": "/api/execution/invoice/cancel", "invoice_id": invoice_id},
                )
                captured["supplier"] = invoice_id
            if parsed.get("type") == "freight_invoice" and not store._row(
                conn, "SELECT freight_invoice_id FROM freight_invoices WHERE transaction_id = %s", (transaction_id,)
            ):
                fid = parsed.get("freight_invoice_id") or f"FR-{_suffix(po['po_id'])}"
                booking_id = (snap["booking"] or {}).get("booking_id")
                conn.execute(
                    """
                    INSERT INTO freight_invoices (
                        freight_invoice_id, transaction_id, booking_id, amount, status
                    ) VALUES (%s,%s,%s,%s,'CAPTURED')
                    """,
                    (fid, transaction_id, booking_id, parsed.get("amount") or 0),
                )
                record_action(
                    conn,
                    agent_name=agent_name,
                    action_type="CAPTURE_FREIGHT_INVOICE",
                    transaction_id=transaction_id,
                    payload={"freight_invoice_id": fid, "amount": parsed.get("amount")},
                    inverse={"endpoint": "/api/execution/freight-invoice/cancel", "freight_invoice_id": fid},
                )
                captured["freight"] = fid
            channels.mark_read(conn, msg["message_id"])
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.INVOICE_CAPTURED.value,
            status="OPEN",
            context_patch={"invoices": captured},
            event="INVOICES_CAPTURED",
        )
        snap = store.snapshot(conn, transaction_id)
        return {
            "supplier_invoice": snap["supplier_invoice"],
            "freight_invoice": snap["freight_invoice"],
            "captured": captured,
        }


def post_match(
    transaction_id: str,
    *,
    agent_name: str = "Finance_Agent",
    human_approved: bool = False,
) -> dict[str, Any]:
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        po, gr, inv, fr = snap["po"], snap["gr"], snap["supplier_invoice"], snap["freight_invoice"]
        if not all([po, gr, inv, fr]):
            return {"error": True, "detail": "Four-way match requires PO, GR, supplier invoice, and freight invoice"}
        variance = policy.match_variance_pct(
            po_price=po["unit_price"],
            invoice_price=inv["unit_price"],
            po_qty=po["quantity"],
            gr_qty=gr["quantity"],
            invoice_qty=inv["quantity"],
            booked_freight=(snap["booking"] or {}).get("freight_cost"),
            freight_amount=fr["amount"],
        )
        max_pct = policy.as_float((snap["policy"] or {}).get("price_deviation_pct"), 5)
        match_id = f"MATCH-{_suffix(po['po_id'])}"
        existing = store._row(conn, "SELECT * FROM invoice_matches WHERE match_id = %s", (match_id,))
        if existing and existing.get("status") == "POSTED":
            return existing
        ctx = (snap["transaction"] or {}).get("context") or {}
        approved = human_approved or bool(ctx.get("human_approved_match"))
        if variance > max_pct and not approved:
            store.set_stage(
                conn,
                transaction_id,
                TransactionStage.AWAITING_HUMAN.value,
                status="AWAITING_HUMAN",
                halt_reason=f"Match variance {variance:.2f}% exceeds policy {max_pct}%",
                context_patch={"match_variance_pct": variance},
                event="MATCH_BREAK",
            )
            if not existing:
                conn.execute(
                    """
                    INSERT INTO invoice_matches (
                        match_id, transaction_id, po_id, gr_id, invoice_id, freight_invoice_id,
                        variance_pct, status
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,'BREAK')
                    """,
                    (match_id, transaction_id, po["po_id"], gr["gr_id"], inv["invoice_id"], fr["freight_invoice_id"], variance),
                )
            return {"pending_approval": True, "variance_pct": variance, "match_id": match_id}

        if existing:
            conn.execute(
                "UPDATE invoice_matches SET status = 'POSTED', variance_pct = %s WHERE match_id = %s",
                (variance, match_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO invoice_matches (
                    match_id, transaction_id, po_id, gr_id, invoice_id, freight_invoice_id,
                    variance_pct, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'POSTED')
                """,
                (match_id, transaction_id, po["po_id"], gr["gr_id"], inv["invoice_id"], fr["freight_invoice_id"], variance),
            )
        conn.execute(
            "UPDATE supplier_invoices SET status = 'MATCHED' WHERE invoice_id = %s",
            (inv["invoice_id"],),
        )
        conn.execute(
            "UPDATE freight_invoices SET status = 'MATCHED' WHERE freight_invoice_id = %s",
            (fr["freight_invoice_id"],),
        )
        record_action(
            conn,
            agent_name=agent_name,
            action_type="POST_MATCH",
            transaction_id=transaction_id,
            payload={"match_id": match_id, "variance_pct": variance},
            inverse={"endpoint": "/api/execution/match/reverse", "match_id": match_id},
        )
        store.write_commercial_memory(
            conn,
            po["vendor_id"],
            po["material_id"],
            float(po["unit_price"]),
            float(inv["unit_price"]),
            transaction_id,
        )
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.SETTLED.value,
            status="SETTLED",
            context_patch={"match_id": match_id, "variance_pct": variance},
            event="SETTLED",
        )
        return store._row(conn, "SELECT * FROM invoice_matches WHERE match_id = %s", (match_id,))


def rebook_after_delay(
    transaction_id: str,
    slip_days: int,
    *,
    agent_name: str = "Logistics_Agent",
    human_approved: bool = False,
) -> dict[str, Any]:
    with get_conn() as conn:
        snap = store.snapshot(conn, transaction_id)
        booking = snap["booking"]
        if not booking:
            return {"error": True, "detail": "No booking to rebook"}
        ctx = (snap["transaction"] or {}).get("context") or {}
        approved = human_approved or bool(ctx.get("human_approved_slip"))
        if not approved and not policy.slip_within_tolerance(slip_days, snap["policy"]):
            store.set_stage(
                conn,
                transaction_id,
                TransactionStage.AWAITING_HUMAN.value,
                status="AWAITING_HUMAN",
                halt_reason=f"Delay of {slip_days} days exceeds slip policy",
                context_patch={"slip_days": slip_days},
                event="DELAY_HITL",
            )
            return {"pending_approval": True, "slip_days": slip_days}

        new_eta = date.today() + timedelta(days=5 + int(slip_days))
        conn.execute(
            "UPDATE bookings SET eta = %s, status = 'REBOOKED' WHERE booking_id = %s",
            (new_eta, booking["booking_id"]),
        )
        channels.mark_all_unread_read(conn, transaction_id)
        record_action(
            conn,
            agent_name=agent_name,
            action_type="REBOOK_SHIPMENT",
            transaction_id=transaction_id,
            payload={"booking_id": booking["booking_id"], "slip_days": slip_days, "new_eta": new_eta.isoformat()},
            inverse={"endpoint": "/api/execution/booking/restore-eta", "booking_id": booking["booking_id"], "eta": booking.get("eta")},
        )
        store.write_exception_memory(
            conn,
            transaction_id,
            "supplier_delay",
            "rebook_within_slip" if not approved else "rebook_after_hitl",
            approved=approved,
            effective=True,
        )
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.IN_TRANSIT.value,
            status="OPEN",
            halt_reason=None,
            context_patch={"rebooked": True, "slip_days": slip_days, "new_eta": new_eta.isoformat()},
            event="REBOOKED",
        )
        return store._row(conn, "SELECT * FROM bookings WHERE booking_id = %s", (booking["booking_id"],))


def hitl_kind(snap: dict) -> str | None:
    tx = snap.get("transaction") or {}
    ctx = tx.get("context") or {}
    stage, status = tx.get("stage"), tx.get("status")
    if ctx.get("hitl_kind") == "model" or ctx.get("hitl_request_id"):
        if status == "AWAITING_HUMAN" or stage == TransactionStage.AWAITING_HUMAN.value:
            return "model"
    if ctx.get("pending_po") or stage == TransactionStage.PO_PENDING_APPROVAL.value:
        return "po"
    if (snap.get("match") or {}).get("status") == "BREAK" or ctx.get("match_variance_pct") is not None:
        if status == "AWAITING_HUMAN" or stage == TransactionStage.AWAITING_HUMAN.value:
            return "match"
    if status == "AWAITING_HUMAN" or stage == TransactionStage.AWAITING_HUMAN.value:
        return "delay"
    return None


def apply_human_decision(
    transaction_id: str,
    action: str,
    *,
    agent_name: str = "operator",
    option: str | None = None,
    note: str | None = None,
    request_id: str | None = None,
    skip_model: bool = False,
) -> dict[str, Any]:
    """Typed HITL: approve_exception | approve_po | cancel | keep_waiting | answer."""
    from agents.execution import hitl as hitl_mod

    action = (action or "").strip().lower()
    if action in {"approve", "approve_exception", "rebook"}:
        action = "approve_exception"
    snap: dict[str, Any] | None = None
    try:
        snap = store.get_snapshot(transaction_id)
    except KeyError:
        snap = None
        if not request_id:
            request_id = transaction_id
    req = hitl_mod.open_request_for(
        transaction_id=transaction_id if snap else None,
        request_id=request_id,
    )
    kind = hitl_kind(snap) if snap else None
    if req and (req.get("kind") == "model" or not kind):
        kind = "model"

    if kind == "model" and not skip_model:
        if not req or req.get("status") != "open":
            return {"error": True, "detail": "No open model HITL request for this job."}
        if action == "keep_waiting":
            return {
                "ok": True,
                "action": action,
                "kind": "model",
                "resumed": False,
                "status": "AWAITING_HUMAN",
                "request_id": req.get("request_id"),
            }
        chosen = option
        if action == "approve_exception" and not chosen:
            chosen = req.get("recommended_option") or ((req.get("options") or [{}])[0].get("id"))
        if action == "cancel" and not chosen:
            chosen = "cancel"
        if action in {"answer", "approve_exception", "cancel"}:
            answered = hitl_mod.answer_model_request(req, option=chosen, note=note, action=action)
            if answered.get("error"):
                return answered
            if answered.get("needs_cancel") and snap:
                cancelled = apply_human_decision(transaction_id, "cancel", skip_model=True)
                cancelled["kind"] = "model"
                cancelled["option"] = answered.get("option")
                cancelled["request_id"] = req.get("request_id")
                return cancelled
            return answered
        return {"error": True, "detail": f"Unknown action {action} for model HITL. Use answer, approve_exception, cancel, or keep_waiting."}

    if not snap:
        return {"error": True, "detail": f"Unknown transaction {transaction_id}"}

    if action == "keep_waiting":
        return {"ok": True, "action": action, "kind": kind, "resumed": False, "status": (snap["transaction"] or {}).get("status")}

    if action == "cancel":
        po = snap.get("po") or {}
        book = snap.get("booking") or {}
        with get_conn() as conn:
            if po.get("po_id"):
                conn.execute("UPDATE exec_purchase_orders SET status = 'CANCELLED' WHERE po_id = %s", (po["po_id"],))
            if book.get("booking_id"):
                conn.execute("UPDATE bookings SET status = 'CANCELLED' WHERE booking_id = %s", (book["booking_id"],))
            store.write_exception_memory(
                conn, transaction_id, "human_cancel", "cancel_po", approved=True, effective=True,
            )
            store.set_stage(
                conn,
                transaction_id,
                "CANCELLED",
                status="CANCELLED",
                halt_reason=None,
                context_patch={"human_decision": "cancel"},
                event="HUMAN_CANCEL",
            )
        try:
            from agents.execution.graph_memory import write_episode
            from agents.memory.store import record_experience

            vendor = po.get("vendor_id") or "VEND-A"
            pr = (snap.get("pr") or {}).get("pr_id") or ""
            tx = snap.get("transaction") or {}
            record_experience(
                flow="p2p_hitl",
                summary=f"Human cancelled {pr}",
                outcome={"action": "cancel"},
                vendor_id=vendor,
                po_id=po.get("po_id"),
                pr_id=pr,
                trace_id=(tx.get("context") or {}).get("trace_id"),
            )
            write_episode(
                incident_key=f"INC-HITL-{pr}",
                incident_name=f"Human cancelled {pr}",
                properties={"root_cause": "policy", "recovery": "cancel", "effective": True},
                rel_type="AFFECTED_BY",
                dst_key=vendor,
                dst_label="Supplier",
                decision_key=f"DEC-CANCEL-{transaction_id.replace('-', '')[:8]}",
                decision_name="Cancel PO",
                decision_props={"action": "cancel", "effective": True},
            )
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "action": "cancel", "kind": kind, "resumed": False, "status": "CANCELLED"}

    if action in {"approve_po", "approve_exception"} and kind == "po":
        posted = approve_pending_po(transaction_id)
        if posted.get("error"):
            return posted
        return {"ok": True, "action": "approve_po", "kind": "po", "resumed": True, "approved_po": posted}

    if action != "approve_exception":
        return {"error": True, "detail": f"Unknown action {action}. Use approve_exception, cancel, or keep_waiting."}

    if kind == "match":
        with get_conn() as conn:
            store.merge_context(conn, transaction_id, {"human_approved_match": True, "human_decision": "approve_exception"})
            store.write_exception_memory(
                conn, transaction_id, "match_break", "post_match_after_hitl",
                approved=True, effective=True,
            )
        posted = post_match(transaction_id, agent_name=agent_name, human_approved=True)
        if posted.get("error"):
            return posted
        _record_hitl_episode(snap, action="approve_exception", recovery="post_match_after_hitl")
        return {"ok": True, "action": action, "kind": "match", "resumed": True, "match": posted}

    if kind != "delay":
        return {"error": True, "detail": "Nothing waiting on a human for this job."}

    ctx = (snap["transaction"] or {}).get("context") or {}
    slip = int(ctx.get("slip_days") or 0)
    with get_conn() as conn:
        store.merge_context(conn, transaction_id, {"human_approved_slip": True, "human_decision": "approve_exception"})
        store.write_exception_memory(
            conn, transaction_id, "supplier_delay", "rebook_after_hitl",
            approved=True, effective=True,
        )
        store.set_stage(
            conn,
            transaction_id,
            TransactionStage.IN_TRANSIT.value,
            status="OPEN",
            halt_reason=None,
            context_patch={"human_approved_slip": True},
            event="HUMAN_APPROVED_SLIP",
        )
    rebooked = rebook_after_delay(transaction_id, slip, agent_name=agent_name, human_approved=True)
    if rebooked.get("error"):
        return rebooked
    _record_hitl_episode(
        snap,
        action="approve_exception",
        recovery="rebook_after_hitl",
        extra={"slip_days": slip},
    )
    return {"ok": True, "action": action, "kind": "delay", "resumed": True, "booking": rebooked}


def _record_hitl_episode(
    snap: dict[str, Any],
    *,
    action: str,
    recovery: str,
    extra: dict[str, Any] | None = None,
) -> None:
    try:
        from agents.execution.graph_memory import write_episode
        from agents.memory.store import record_experience

        extra = extra or {}
        vendor = (snap.get("po") or {}).get("vendor_id") or "VEND-A"
        pr = (snap.get("pr") or {}).get("pr_id") or ""
        tx = snap.get("transaction") or {}
        tid = tx.get("transaction_id") or ""
        record_experience(
            flow="p2p_hitl",
            summary=f"Human {action} {pr}",
            outcome={"action": action, "recovery": recovery, **extra},
            vendor_id=vendor,
            po_id=(snap.get("po") or {}).get("po_id"),
            pr_id=pr,
            trace_id=(tx.get("context") or {}).get("trace_id"),
        )
        write_episode(
            incident_key=f"INC-HITL-{pr}",
            incident_name=f"Human {action} {pr}",
            properties={"root_cause": extra.get("root_cause") or recovery, "recovery": recovery, "effective": True, **extra},
            rel_type="AFFECTED_BY",
            dst_key=vendor,
            dst_label="Supplier",
            decision_key=f"DEC-HITL-{action[:6].upper()}-{tid.replace('-', '')[:8]}",
            decision_name=action,
            decision_props={"action": recovery, "effective": True, **extra},
        )
    except Exception:  # noqa: BLE001
        pass


def cancel_document(kind: str, key: str, transaction_id: str | None = None) -> dict[str, Any]:
    """Inverse helpers used by the action log (compensation)."""
    with get_conn() as conn:
        if kind == "rfq":
            conn.execute("UPDATE rfqs SET status = 'CANCELLED' WHERE rfq_id = %s", (key,))
        elif kind == "po":
            conn.execute("UPDATE exec_purchase_orders SET status = 'CANCELLED' WHERE po_id = %s", (key,))
        elif kind == "booking":
            conn.execute("UPDATE bookings SET status = 'CANCELLED' WHERE booking_id = %s", (key,))
        elif kind == "gr":
            conn.execute("DELETE FROM goods_receipts_exec WHERE gr_id = %s", (key,))
        elif kind == "invoice":
            conn.execute("UPDATE supplier_invoices SET status = 'CANCELLED' WHERE invoice_id = %s", (key,))
        elif kind == "freight":
            conn.execute("UPDATE freight_invoices SET status = 'CANCELLED' WHERE freight_invoice_id = %s", (key,))
        elif kind == "match":
            conn.execute("UPDATE invoice_matches SET status = 'REVERSED' WHERE match_id = %s", (key,))
        else:
            return {"error": True, "detail": f"Unknown inverse kind {kind}"}
        return {"ok": True, "kind": kind, "key": key}
