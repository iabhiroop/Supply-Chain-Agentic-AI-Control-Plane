"""Operations dashboard reads — KPIs, exceptions, incidents, reference scenario."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agents.chat.lookup import _clean
from agents.config.db import fetch_all, fetch_one
from agents.ops import detector

router = APIRouter(tags=["Operations"])

REFERENCE_ORDER_ID = 10248
REFERENCE_PRODUCT_ID = 11
REFERENCE_BIN = "A1-01"


def _rows(query: str, params: tuple = ()) -> list[dict]:
    return [_clean(r) for r in fetch_all(query, params)]


@router.get("/v1/ops/overview")
def ops_overview():
    """Snapshot for the warehouse operations dashboard."""
    kpis = fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM incidents
             WHERE status IN ('OPEN','IN_PROGRESS','AWAITING_APPROVAL')) AS open_incidents,
          (SELECT COUNT(*) FROM bin_stock WHERE quantity = 0) AS empty_bins,
          (SELECT COUNT(*) FROM shipments s
             WHERE s.promised_delivery IS NOT NULL AND s.estimated_delivery IS NOT NULL
               AND s.estimated_delivery > s.promised_delivery
               AND s.status NOT IN ('DELIVERED','CANCELLED')) AS late_shipments,
          (SELECT COUNT(*) FROM shipments
             WHERE status IN ('PLANNED','TENDERED','DELAYED','IN_TRANSIT')) AS active_shipments,
          (SELECT COUNT(*) FROM shipments s
             JOIN carrier_rates cr ON cr.carrier_id = s.carrier_id
              AND lower(cr.region) = lower(s.destination_country)
             WHERE cr.capacity_available = FALSE
               AND s.status NOT IN ('DELIVERED','CANCELLED')) AS dropped_loads
        """
    )

    reference = fetch_one(
        """
        SELECT o.order_id, o.customer_id, c.company_name, c.contact_name, c.country,
               p.product_id, p.product_name,
               bs.bin_id, bs.quantity AS bin_qty,
               sh.status AS shipment_status, cr.carrier_name,
               sh.promised_delivery, sh.estimated_delivery,
               CASE WHEN sh.promised_delivery IS NULL OR sh.estimated_delivery IS NULL THEN NULL
                    ELSE EXTRACT(DAY FROM (sh.estimated_delivery - sh.promised_delivery))::int
               END AS days_late
        FROM orders o
        JOIN customers c ON c.customer_id = o.customer_id
        JOIN products p ON p.product_id = %s
        LEFT JOIN bin_stock bs ON bs.product_id = p.product_id AND bs.bin_id = %s
        LEFT JOIN LATERAL (
            SELECT s.status, s.promised_delivery, s.estimated_delivery, s.carrier_id
            FROM shipments s WHERE s.order_id = o.order_id
            ORDER BY s.created_at DESC LIMIT 1
        ) sh ON TRUE
        LEFT JOIN carriers cr ON cr.carrier_id = sh.carrier_id
        WHERE o.order_id = %s
        """,
        (REFERENCE_PRODUCT_ID, REFERENCE_BIN, REFERENCE_ORDER_ID),
    )

    empty_bins = _rows(
        """
        SELECT bs.bin_id, bs.product_id, p.product_name, bs.quantity,
               sb.warehouse_id, sb.zone
        FROM bin_stock bs
        JOIN products p ON p.product_id = bs.product_id
        JOIN storage_bins sb ON sb.bin_id = bs.bin_id
        WHERE bs.quantity = 0
        ORDER BY sb.warehouse_id, bs.bin_id
        LIMIT 15
        """
    )

    late = _rows(
        """
        SELECT s.shipment_id, s.order_id, c.carrier_name, s.status, s.service_level,
               s.destination_country, s.promised_delivery, s.estimated_delivery,
               EXTRACT(DAY FROM (s.estimated_delivery - s.promised_delivery))::int AS days_late
        FROM shipments s
        JOIN carriers c ON c.carrier_id = s.carrier_id
        WHERE s.promised_delivery IS NOT NULL AND s.estimated_delivery IS NOT NULL
          AND s.estimated_delivery > s.promised_delivery
          AND s.status NOT IN ('DELIVERED','CANCELLED')
        ORDER BY days_late DESC
        LIMIT 15
        """
    )

    incidents = _rows(
        """
        SELECT incident_id, event_type, status, severity, order_id, product_id,
               resolution_summary, created_at, resolved_at
        FROM incidents
        ORDER BY created_at DESC
        LIMIT 20
        """
    )

    scenarios = [
        {
            "id": "empty_bin_pick",
            "title": "Empty bin on pick",
            "persona": "Warehouse worker",
            "agents": "Stock replenish from C1-01; skip expedite if the line is covered",
            "trigger": "Picker finds A1-01 empty for product 11 on order 10248.",
            "steps": [
                "Confirm on-hand. Alternate stock also sits in C1-01 — ask the copilot.",
                "Report a stock mismatch (counted 0).",
                "Confirm the card. Smart Stock should replenish from C1-01 and skip expedite if the line is covered.",
            ],
            "ask": "How many units of product 11 are in bin A1-01, and is there stock in any other bin?",
            "act": "Stock mismatch: bin A1-01 is empty for Queso Cabrales on order 10248, counted 0.",
        },
        {
            "id": "second_mismatch",
            "title": "Second empty pick face",
            "persona": "Warehouse worker",
            "agents": "Stock → Tendering → Dispatch → Issue Resolution",
            "trigger": "B1-02 is empty for the first line on order 10252 (SUPRD).",
            "steps": [
                "Open Floor exceptions and inspect B1-02.",
                "Ask the copilot what the ledger last recorded for that product.",
                "Report the mismatch on order 10252 so a second incident can run without colliding with 10248.",
            ],
            "ask": "What is the on-hand quantity in bin B1-02?",
            "act": "Stock mismatch: bin B1-02 is empty on order 10252, counted 0.",
        },
        {
            "id": "carrier_rejected",
            "title": "Carrier dropped the load",
            "persona": "Warehouse manager",
            "agents": "Tendering → Dispatch → Issue Resolution (stock skipped)",
            "trigger": "Order 10251 (VICTE, France) is on Federal Shipping, which has no France capacity.",
            "steps": [
                "Ask which France carriers still have capacity.",
                "Report that the carrier dropped the load — do not invent a stock count.",
            ],
            "ask": "Which carriers serve France and still have capacity?",
            "act": "Federal Shipping dropped the load on order 10251, no capacity.",
        },
        {
            "id": "germany_delay",
            "title": "Lane delay, capacity still open",
            "persona": "Warehouse manager",
            "agents": "Tendering → Dispatch → Issue Resolution (stock skipped)",
            "trigger": "Order 10249 (TOMSP, Germany) is late on United Package; Euro Freight has capacity.",
            "steps": [
                "Ask days late and which German carriers can recover the promise.",
                "Report the shipment as delayed so tendering can re-score cost vs SLA.",
            ],
            "ask": "Is shipment for order 10249 late, and which carriers serve Germany?",
            "act": "Shipment delayed on order 10249, missed window.",
        },
        {
            "id": "in_transit_late",
            "title": "In-transit SLA notification",
            "persona": "Warehouse manager",
            "agents": "Tendering → Dispatch → Issue Resolution (stock skipped)",
            "trigger": "Order 10250 (HANAR, Brazil) is IN_TRANSIT and already past the promise.",
            "steps": [
                "Ask SLA terms and days late (manager role).",
                "Confirm a delay event if a faster Brazil carrier should take the load.",
            ],
            "ask": "Is shipment for order 10250 late, and what SLA terms apply?",
            "act": "Shipment delayed on order 10250, missed window.",
        },
        {
            "id": "asn_gr",
            "title": "Inbound ASN discrepancy",
            "persona": "Warehouse manager",
            "agents": "Inbound Receiving → Issue Resolution (supplier)",
            "trigger": "ASN-1001 expected 20 of product 11, dock counted 0 in RCV-01.",
            "steps": [
                "Ask expected vs counted on ASN-1001 (manager role).",
                "Confirm the goods-receipt discrepancy. No carrier is booked.",
            ],
            "ask": "What is expected vs counted on ASN-1001?",
            "act": "ASN-1001 goods receipt discrepancy, counted 0.",
        },
        {
            "id": "incident_follow",
            "title": "Follow an open incident",
            "persona": "Warehouse manager",
            "agents": "None new — inspect prior run",
            "trigger": "A workflow is already running or resolved.",
            "steps": [
                "Open Incidents and select the row.",
                "Ask what happened. Do not confirm a second identical event — it is idempotent.",
            ],
            "ask": "What's happening on the incident?",
            "act": None,
        },
    ]

    signals: list[dict] = []
    try:
        detector.scan_and_upsert()
        signals = [_clean(r) for r in detector.list_signals("OPEN", 20)]
    except Exception:  # noqa: BLE001
        signals = []

    return {
        "kpis": _clean(kpis) or {},
        "reference": _clean(reference),
        "empty_bins": empty_bins,
        "late_shipments": late,
        "incidents": incidents,
        "signals": signals,
        "scenarios": scenarios,
        "site": {"warehouse": "WH-FR-01", "name": "Le Havre outbound"},
    }


@router.get("/v1/ops/signals")
def list_open_signals():
    try:
        detector.scan_and_upsert()
        return {"signals": [_clean(r) for r in detector.list_signals("OPEN", 50)]}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Detector tables not ready: {exc}") from exc


@router.post("/v1/ops/signals/{signal_id}/confirm")
async def confirm_detected_signal(signal_id: str, wait: bool = False):
    result = await detector.confirm_signal(signal_id, wait=wait)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error") or "cannot confirm")
    return result


@router.post("/v1/ops/signals/{signal_id}/dismiss")
def dismiss_detected_signal(signal_id: str):
    row = detector.dismiss_signal(signal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown signal")
    return {"signal_id": str(row["signal_id"]), "status": row["status"]}
