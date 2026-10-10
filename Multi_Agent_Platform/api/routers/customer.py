"""Customer / CRM endpoints backed by PostgreSQL."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.database.db import fetch_all, fetch_one, get_conn
from api.database.schemas import (
    CustomerResponse,
    EmailRequest,
    EmailResponse,
    OrderLineResponse,
    SlaTermResponse,
)

router = APIRouter(prefix="/api", tags=["Customer"])


@router.get("/customers/{order_id}", response_model=CustomerResponse)
def get_customer_by_order(order_id: int):
    """Customer and ship-to details for an order.

    The email address is real data in this store (synthesised deterministically
    at migration time) so the Issue Resolution agent never has to invent one.
    """
    row = fetch_one(
        """
        SELECT c.customer_id, c.company_name, c.contact_name, c.email, c.phone, c.country,
               o.ship_name, o.ship_city, o.ship_region, o.ship_country, o.order_id
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        WHERE o.order_id = %s
        """,
        (order_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No customer found for order {order_id}")
    return row


@router.get("/customers/{order_id}/sla", response_model=list[SlaTermResponse])
def get_sla_terms(order_id: int):
    """Contractual SLA terms that apply to an order.

    Grounds customer-facing promises in the actual contract instead of letting
    the model guess what was committed.
    """
    rows = fetch_all(
        """
        SELECT ct.contract_id, st.metric, st.threshold_days, st.penalty_pct, st.notes
        FROM orders o
        JOIN contracts ct ON ct.customer_id = o.customer_id
        JOIN sla_terms st ON st.contract_id = ct.contract_id
        WHERE o.order_id = %s
        ORDER BY st.metric
        """,
        (order_id,),
    )
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No contractual SLA terms on file for order {order_id}"
        )
    return rows


@router.get("/orders/{order_id}/lines", response_model=list[OrderLineResponse])
def get_order_lines(order_id: int):
    """Line items on an order, so an agent can see what is actually affected."""
    rows = fetch_all(
        """
        SELECT od.product_id, p.product_name, od.quantity, od.unit_price
        FROM order_details od
        JOIN products p ON p.product_id = od.product_id
        WHERE od.order_id = %s
        ORDER BY od.product_id
        """,
        (order_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No order lines found for order {order_id}")
    return rows


@router.post("/communications/email", response_model=EmailResponse)
def send_email(req: EmailRequest):
    """Log an outbound customer email.

    Non-production never actually sends: the message is persisted so it can be
    inspected, audited, and scored by Layer 4.
    """
    with get_conn() as conn:
        incident_id = None
        if req.incident_id:
            exists = conn.execute(
                "SELECT 1 FROM incidents WHERE incident_id = %s", (req.incident_id,)
            ).fetchone()
            incident_id = req.incident_id if exists else None

        row = conn.execute(
            "INSERT INTO communications (incident_id, order_id, channel, to_address, subject, body, status, party) "
            "VALUES (%s, %s, 'EMAIL', %s, %s, %s, 'LOGGED', %s) RETURNING communication_id",
            (incident_id, req.order_id, req.to_email, req.subject, req.body, req.party or "customer"),
        ).fetchone()

    print(f"\n--- MOCK EMAIL (logged, not sent) ---\nTo: {req.to_email}\n"
          f"Subject: {req.subject}\n{req.body}\n-------------------------------------\n")

    return EmailResponse(
        status="success",
        communication_id=row["communication_id"],
        to_address=req.to_email,
    )
