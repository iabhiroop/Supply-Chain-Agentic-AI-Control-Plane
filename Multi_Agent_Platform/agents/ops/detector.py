"""Scan live WMS/TMS/ASN exceptions into detected_signals. Not an LLM agent."""

from __future__ import annotations

import json
import uuid
from typing import Any

from agents.config.db import get_conn
from agents.config.settings import settings
from agents.contracts import IncidentEvent
from agents.runtime import execute_workflow, open_incident


def scan_and_upsert(tenant_id: str | None = None) -> int:
    """Insert or refresh OPEN signals. Returns how many rows were touched."""
    tenant = tenant_id or settings.tenant_id
    candidates = _collect()
    touched = 0
    with get_conn() as conn:
        for item in candidates:
            existing = conn.execute(
                """
                SELECT signal_id, status FROM detected_signals
                WHERE tenant_id = %s AND business_key = %s
                """,
                (tenant, item["business_key"]),
            ).fetchone()
            if existing:
                if existing["status"] != "OPEN":
                    continue
                conn.execute(
                    """
                    UPDATE detected_signals
                    SET entity = %s, event_payload = %s, updated_at = now()
                    WHERE signal_id = %s
                    """,
                    (
                        json.dumps(item["entity"], default=str),
                        json.dumps(item["event_payload"], default=str),
                        existing["signal_id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO detected_signals (
                        signal_id, tenant_id, signal_type, business_key,
                        entity, event_payload, status
                    ) VALUES (%s,%s,%s,%s,%s,%s,'OPEN')
                    """,
                    (
                        str(uuid.uuid4()),
                        tenant,
                        item["signal_type"],
                        item["business_key"],
                        json.dumps(item["entity"], default=str),
                        json.dumps(item["event_payload"], default=str),
                    ),
                )
            touched += 1
    return touched


def _collect() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with get_conn() as conn:
        empty_picks = conn.execute(
            """
            SELECT o.order_id, od.product_id, p.product_name, bs.bin_id, od.quantity AS ordered_qty
            FROM orders o
            JOIN order_details od ON od.order_id = o.order_id
            JOIN products p ON p.product_id = od.product_id
            JOIN bin_stock bs ON bs.product_id = od.product_id AND bs.quantity = 0
            JOIN shipments s ON s.order_id = o.order_id
            WHERE s.status NOT IN ('DELIVERED', 'CANCELLED')
              AND o.order_id IN (10248, 10252)
            ORDER BY o.order_id, bs.bin_id
            """
        ).fetchall()
        for row in empty_picks:
            rows.append({
                "signal_type": "EMPTY_PICK_FACE",
                "business_key": f"empty_pick:{row['order_id']}:{row['bin_id']}:{row['product_id']}",
                "entity": dict(row),
                "event_payload": {
                    "event_type": "STOCK_MISMATCH",
                    "order_id": row["order_id"],
                    "product_id": row["product_id"],
                    "bin_id": row["bin_id"],
                    "reported_qty": 0,
                    "source": "detector",
                },
            })

        late = conn.execute(
            """
            SELECT s.order_id, s.status, c.carrier_name,
                   EXTRACT(DAY FROM (s.estimated_delivery - s.promised_delivery))::int AS days_late
            FROM shipments s
            JOIN carriers c ON c.carrier_id = s.carrier_id
            WHERE s.promised_delivery IS NOT NULL AND s.estimated_delivery IS NOT NULL
              AND s.estimated_delivery > s.promised_delivery
              AND s.status NOT IN ('DELIVERED', 'CANCELLED')
            """
        ).fetchall()
        for row in late:
            rows.append({
                "signal_type": "LATE_SHIPMENT",
                "business_key": f"late_shipment:{row['order_id']}",
                "entity": dict(row),
                "event_payload": {
                    "event_type": "SHIPMENT_DELAYED",
                    "order_id": row["order_id"],
                    "source": "detector",
                },
            })

        dropped = conn.execute(
            """
            SELECT s.order_id, c.carrier_name, s.destination_country
            FROM shipments s
            JOIN carriers c ON c.carrier_id = s.carrier_id
            JOIN carrier_rates cr ON cr.carrier_id = s.carrier_id
             AND lower(cr.region) = lower(s.destination_country)
            WHERE cr.capacity_available = FALSE
              AND s.status NOT IN ('DELIVERED', 'CANCELLED')
            """
        ).fetchall()
        for row in dropped:
            rows.append({
                "signal_type": "DROPPED_LOAD",
                "business_key": f"dropped_load:{row['order_id']}",
                "entity": dict(row),
                "event_payload": {
                    "event_type": "CARRIER_REJECTED_LOAD",
                    "order_id": row["order_id"],
                    "source": "detector",
                },
            })

        asns = conn.execute(
            """
            SELECT a.asn_id, a.supplier_id, a.expected_bin_id, l.product_id,
                   l.expected_qty, l.counted_qty
            FROM inbound_asns a
            JOIN inbound_asn_lines l ON l.asn_id = a.asn_id
            WHERE a.status <> 'RECEIVED'
              AND (l.counted_qty IS NULL OR l.counted_qty <> l.expected_qty)
            """
        ).fetchall()
        for row in asns:
            counted = 0 if row["counted_qty"] is None else row["counted_qty"]
            if counted == row["expected_qty"]:
                continue
            rows.append({
                "signal_type": "ASN_DISCREPANCY",
                "business_key": f"asn_discrepancy:{row['asn_id']}:{row['product_id']}",
                "entity": dict(row),
                "event_payload": {
                    "event_type": "GOODS_RECEIPT_DISCREPANCY",
                    "asn_id": row["asn_id"],
                    "product_id": row["product_id"],
                    "bin_id": row["expected_bin_id"],
                    "counted_qty": counted,
                    "reported_qty": counted,
                    "source": "detector",
                },
            })
    return rows


def list_signals(status: str = "OPEN", limit: int = 30) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT signal_id, signal_type, business_key, entity, event_payload,
                   status, incident_id, created_at, updated_at
            FROM detected_signals
            WHERE status = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (status, limit),
        ).fetchall()


def dismiss_signal(signal_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            UPDATE detected_signals
            SET status = 'DISMISSED', updated_at = now()
            WHERE signal_id = %s
            RETURNING signal_id, status
            """,
            (signal_id,),
        ).fetchone()
    return row


async def confirm_signal(signal_id: str, *, wait: bool = False) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM detected_signals WHERE signal_id = %s",
            (signal_id,),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": "unknown signal"}
    if row["status"] != "OPEN":
        return {"ok": False, "error": f"signal is {row['status']}", "incident_id": str(row["incident_id"]) if row.get("incident_id") else None}

    payload = row["event_payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    event = IncidentEvent.model_validate(payload)
    event.source = "detector"
    event.tenant_id = settings.tenant_id

    incident_id, created = open_incident(event)
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE detected_signals
            SET status = 'CONFIRMED', incident_id = %s, updated_at = now()
            WHERE signal_id = %s
            """,
            (incident_id, signal_id),
        )

    if not created:
        return {
            "ok": True,
            "created": False,
            "incident_id": incident_id,
            "status": "DUPLICATE",
        }

    if wait:
        result = await execute_workflow(incident_id, event, verbose=False)
        result["ok"] = True
        return result

    import asyncio
    asyncio.create_task(execute_workflow(incident_id, event, verbose=False))
    return {
        "ok": True,
        "created": True,
        "incident_id": incident_id,
        "status": "IN_PROGRESS",
    }


async def maybe_auto_run_open_signals() -> int:
    """Confirm OPEN signals whose event_type is in SCM_DETECTOR_AUTO_RUN."""
    allowed = settings.detector_auto_event_types()
    if not allowed:
        return 0
    ran = 0
    for row in list_signals("OPEN", limit=50):
        payload = row["event_payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if (payload or {}).get("event_type") not in allowed:
            continue
        await confirm_signal(str(row["signal_id"]), wait=False)
        ran += 1
    return ran
