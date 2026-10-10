"""Transport / TMS endpoints backed by PostgreSQL."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from api.database.db import fetch_all, get_conn, record_action
from api.database.schemas import (
    CarrierRateResponse,
    ShipmentResponse,
    ShipmentTenderRequest,
    ShipmentTenderResponse,
)

router = APIRouter(prefix="/api", tags=["Transport"])


def _parse_delivery(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise HTTPException(
        status_code=422,
        detail=f"Unrecognised date {value!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS.",
    )


@router.get("/shipments/{order_id}", response_model=ShipmentResponse)
def get_shipment(order_id: int):
    """Active shipment for an order, including how late it currently is.

    `days_late` is computed here rather than left to the model: date arithmetic
    belongs in code (plan.md §3.3).
    """
    row = fetch_all(
        """
        SELECT s.shipment_id, s.order_id, s.carrier_id, c.carrier_name,
               c.mode AS carrier_mode, s.status, s.service_level,
               s.origin_warehouse_id, s.destination_country,
               s.promised_delivery, s.estimated_delivery, s.freight_cost,
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
    if not row:
        raise HTTPException(status_code=404, detail=f"No shipment found for order {order_id}")
    return row[0]


@router.get("/carrier_rates/{region}", response_model=list[CarrierRateResponse])
def get_carrier_rates(region: str):
    """Carriers serving a region, with rates, transit times and capacity.

    Used by the Tendering agent to benchmark freight cost against the timeline.
    Carriers without capacity are still returned, flagged, so the agent can see
    why an option is unavailable rather than silently missing it.
    """
    rows = fetch_all(
        """
        SELECT cr.carrier_id, c.carrier_name, c.mode, cr.region,
               cr.base_rate, cr.expedited_rate,
               cr.base_transit_days, cr.expedited_transit_days,
               cr.capacity_available
        FROM carrier_rates cr
        JOIN carriers c ON c.carrier_id = cr.carrier_id
        WHERE lower(cr.region) = lower(%s)
        ORDER BY cr.expedited_rate
        """,
        (region,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No carrier rates found for region {region!r}")
    return rows


@router.post("/shipments/tender", response_model=ShipmentTenderResponse)
def tender_shipment(tender: ShipmentTenderRequest):
    """Assign a new carrier and estimated delivery date to a shipment.

    Rejects carriers that do not serve the destination or have no capacity, so
    an agent cannot book an impossible option. Records an inverse action that
    restores the previous carrier and date.
    """
    new_delivery = _parse_delivery(tender.new_estimated_delivery)

    with get_conn() as conn:
        shipment = conn.execute(
            """
            SELECT s.shipment_id, s.carrier_id, s.destination_country, s.promised_delivery,
                   s.estimated_delivery, s.service_level, c.carrier_name
            FROM shipments s
            JOIN carriers c ON c.carrier_id = s.carrier_id
            WHERE s.order_id = %s
            ORDER BY s.created_at DESC
            LIMIT 1
            FOR UPDATE OF s
            """,
            (tender.order_id,),
        ).fetchone()
        if shipment is None:
            raise HTTPException(status_code=404,
                                detail=f"No shipment found for order {tender.order_id}")

        carrier = conn.execute(
            "SELECT carrier_name FROM carriers WHERE carrier_id = %s", (tender.new_carrier_id,)
        ).fetchone()
        if carrier is None:
            raise HTTPException(status_code=404,
                                detail=f"Carrier {tender.new_carrier_id} does not exist")

        rate = conn.execute(
            """
            SELECT expedited_rate, base_rate, capacity_available
            FROM carrier_rates
            WHERE carrier_id = %s AND lower(region) = lower(%s)
            """,
            (tender.new_carrier_id, shipment["destination_country"] or ""),
        ).fetchone()
        if rate is None:
            raise HTTPException(
                status_code=409,
                detail=(f"Carrier {carrier['carrier_name']} has no rate for region "
                        f"{shipment['destination_country']!r} and cannot be tendered."),
            )
        # Capacity is a forward-booking constraint only. A compensating
        # transaction must still be able to restore the previous carrier, even
        # though that carrier has no capacity now.
        if not rate["capacity_available"] and not tender.is_compensation:
            raise HTTPException(
                status_code=409,
                detail=(f"Carrier {carrier['carrier_name']} has no available capacity "
                        f"for {shipment['destination_country']}."),
            )

        cost = float(rate["expedited_rate"] if tender.service_level.upper() == "EXPEDITED"
                     else rate["base_rate"])

        conn.execute(
            """
            UPDATE shipments
            SET carrier_id = %s, status = 'TENDERED', estimated_delivery = %s,
                service_level = %s, freight_cost = %s, updated_at = now()
            WHERE shipment_id = %s
            """,
            (tender.new_carrier_id, new_delivery, tender.service_level.upper(),
             cost, shipment["shipment_id"]),
        )

        action_id = record_action(
            conn,
            agent_name=tender.agent_name,
            action_type="SHIPMENT_TENDER",
            payload={
                "order_id": tender.order_id,
                "shipment_id": shipment["shipment_id"],
                "new_carrier_id": tender.new_carrier_id,
                "new_estimated_delivery": new_delivery.isoformat(),
                "service_level": tender.service_level.upper(),
                "freight_cost": cost,
                "reason": tender.reason,
                "is_compensation": tender.is_compensation,
            },
            inverse={
                "endpoint": "/api/shipments/tender",
                "order_id": tender.order_id,
                "new_carrier_id": shipment["carrier_id"],
                "new_estimated_delivery": (
                    shipment["estimated_delivery"].isoformat()
                    if shipment["estimated_delivery"] else None
                ),
                "service_level": shipment["service_level"],
                "reason": f"Compensation: restore carrier {shipment['carrier_name']}",
                "is_compensation": True,
            },
            incident_id=tender.incident_id,
        )

        promised = shipment["promised_delivery"]
        meets_promise = promised is None or new_delivery <= promised

    return ShipmentTenderResponse(
        status="success",
        action_id=action_id,
        order_id=tender.order_id,
        shipment_id=shipment["shipment_id"],
        previous_carrier_id=shipment["carrier_id"],
        previous_carrier_name=shipment["carrier_name"],
        new_carrier_id=tender.new_carrier_id,
        new_carrier_name=carrier["carrier_name"],
        new_estimated_delivery=new_delivery,
        freight_cost=cost,
        meets_promise=meets_promise,
    )
