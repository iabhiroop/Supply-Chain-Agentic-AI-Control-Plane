"""Read-only operational facts for copilot answers and event cards."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from agents.config.db import fetch_all, fetch_one


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _clean(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: _jsonable(v) for k, v in row.items()}


def order_facts(order_id: int) -> dict[str, Any]:
    order = fetch_one(
        """
        SELECT o.order_id, o.customer_id, c.company_name, c.contact_name, c.email,
               c.country, o.ship_city, o.ship_country, o.required_date, o.shipped_date
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        WHERE o.order_id = %s
        """,
        (order_id,),
    )
    lines = fetch_all(
        """
        SELECT od.product_id, p.product_name, od.quantity, od.unit_price
        FROM order_details od
        JOIN products p ON p.product_id = od.product_id
        WHERE od.order_id = %s
        ORDER BY od.product_id
        """,
        (order_id,),
    )
    sla = fetch_all(
        """
        SELECT st.metric, st.threshold_days, st.penalty_pct, st.notes
        FROM orders o
        JOIN contracts ct ON ct.customer_id = o.customer_id
        JOIN sla_terms st ON st.contract_id = ct.contract_id
        WHERE o.order_id = %s
        """,
        (order_id,),
    )
    shipment = fetch_one(
        """
        SELECT s.shipment_id, s.status, c.carrier_name, s.service_level,
               s.promised_delivery, s.estimated_delivery, s.freight_cost,
               s.destination_country,
               CASE WHEN s.promised_delivery IS NULL OR s.estimated_delivery IS NULL THEN NULL
                    ELSE EXTRACT(DAY FROM (s.estimated_delivery - s.promised_delivery))::int
               END AS days_late
        FROM shipments s
        JOIN carriers c ON c.carrier_id = s.carrier_id
        WHERE s.order_id = %s
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        (order_id,),
    )
    return {
        "order": _clean(order),
        "lines": [_clean(r) for r in lines],
        "sla": [_clean(r) for r in sla],
        "shipment": _clean(shipment),
    }


def product_facts(product_id: int) -> dict[str, Any]:
    product = fetch_one(
        "SELECT product_id, product_name, units_in_stock, reorder_level FROM products WHERE product_id = %s",
        (product_id,),
    )
    bins = fetch_all(
        """
        SELECT bs.bin_id, bs.quantity, sb.warehouse_id, sb.zone
        FROM bin_stock bs
        JOIN storage_bins sb ON sb.bin_id = bs.bin_id
        WHERE bs.product_id = %s
        ORDER BY bs.bin_id
        """,
        (product_id,),
    )
    ledger = fetch_all(
        """
        SELECT transaction_type, quantity, reason, bin_id, created_at
        FROM stock_ledger
        WHERE product_id = %s
        ORDER BY created_at DESC, transaction_id DESC
        LIMIT 8
        """,
        (product_id,),
    )
    return {
        "product": _clean(product),
        "bins": [_clean(r) for r in bins],
        "recent_ledger": [_clean(r) for r in ledger],
    }


def incident_snapshot(incident_id: str) -> dict[str, Any] | None:
    incident = fetch_one("SELECT * FROM incidents WHERE incident_id = %s", (incident_id,))
    if incident is None:
        return None
    runs = fetch_all(
        """
        SELECT agent_name, status, confidence, llm_calls, tool_calls, started_at, ended_at, result
        FROM agent_runs WHERE incident_id = %s ORDER BY started_at
        """,
        (incident_id,),
    )
    actions = fetch_all(
        """
        SELECT action_id, agent_name, action_type, payload, inverse, status, created_at
        FROM actions
        WHERE incident_id = %s ORDER BY created_at
        """,
        (incident_id,),
    )
    comms = fetch_all(
        """
        SELECT to_address, subject, status, created_at, party FROM communications
        WHERE incident_id = %s ORDER BY created_at
        """,
        (incident_id,),
    )
    return {
        "incident": _clean(incident),
        "agent_runs": [_clean(r) for r in runs],
        "actions": [_clean(r) for r in actions],
        "communications": [_clean(r) for r in comms],
    }


def gather_facts(*, order_id: int | None, product_id: int | None, incident_id: str | None) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    if order_id:
        facts["order"] = order_facts(order_id)
    if product_id:
        facts["product"] = product_facts(product_id)
    if incident_id:
        snap = incident_snapshot(incident_id)
        if snap:
            facts["incident"] = snap
    return facts
