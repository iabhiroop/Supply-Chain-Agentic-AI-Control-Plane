"""Read queries shared by the checker and the HTTP API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from twin.db import fetch_all, fetch_one


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def days_of_cover(material_id: str = "MAT-SEMI-1") -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT i.plant_id, p.name AS plant_name, p.country,
               i.material_id, i.on_hand, i.safety_stock, i.daily_demand, i.days_of_cover,
               d.qty AS demand_qty, d.horizon_days
        FROM inventory_positions i
        JOIN plants p ON p.plant_id = i.plant_id
        LEFT JOIN independent_demand d
          ON d.plant_id = i.plant_id AND d.material_id = i.material_id
        WHERE i.material_id = %s
        ORDER BY i.days_of_cover NULLS LAST, i.plant_id
        """,
        (material_id,),
    )
    out = []
    for r in rows:
        cover = _num(r["days_of_cover"])
        horizon = r.get("horizon_days")
        out.append(
            {
                **r,
                "daily_demand": _num(r["daily_demand"]),
                "days_of_cover": cover,
                "demand_exceeds_cover": (
                    horizon is not None and cover is not None and int(horizon) > cover
                ),
            }
        )
    return out


def open_pos(*, vendor_id: str | None = None, status: str = "OPEN") -> list[dict[str, Any]]:
    sql = """
        SELECT po_id, vendor_id, material_id, plant_id, quantity, unit_price,
               promise_date, status, reallocatable_qty
        FROM twin_purchase_orders
        WHERE status = %s
    """
    params: list[Any] = [status]
    if vendor_id:
        sql += " AND vendor_id = %s"
        params.append(vendor_id)
    sql += " ORDER BY po_id"
    rows = fetch_all(sql, tuple(params))
    for r in rows:
        r["unit_price"] = _num(r["unit_price"])
        if r.get("promise_date"):
            r["promise_date"] = str(r["promise_date"])
    return rows


def news_signals(*, entity_id: str | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT signal_id, signal_type, entity_id, headline, payload, event_time
        FROM twin_signals
        WHERE signal_type = 'news'
    """
    params: list[Any] = []
    if entity_id:
        sql += " AND entity_id = %s"
        params.append(entity_id)
    sql += " ORDER BY event_time DESC"
    rows = fetch_all(sql, tuple(params))
    for r in rows:
        if r.get("event_time"):
            r["event_time"] = r["event_time"].isoformat()
    return rows


def plants_if_abc_down() -> list[dict[str, Any]]:
    """SQL answer: plants stocking ABC's material, with cover and open POs."""
    rows = fetch_all(
        """
        SELECT p.plant_id, p.name AS plant_name, p.country,
               i.days_of_cover, d.horizon_days,
               (SELECT COALESCE(SUM(po.quantity), 0)
                  FROM twin_purchase_orders po
                 WHERE po.plant_id = p.plant_id
                   AND po.vendor_id = 'VEND-ABC'
                   AND po.material_id = 'MAT-SEMI-1'
                   AND po.status = 'OPEN') AS open_qty_from_abc
        FROM inventory_positions i
        JOIN plants p ON p.plant_id = i.plant_id
        LEFT JOIN independent_demand d
          ON d.plant_id = i.plant_id AND d.material_id = i.material_id
        WHERE i.material_id = 'MAT-SEMI-1'
        ORDER BY i.days_of_cover NULLS LAST
        """
    )
    for r in rows:
        r["days_of_cover"] = _num(r["days_of_cover"])
        r["open_qty_from_abc"] = int(r["open_qty_from_abc"] or 0)
    return rows


def vendor_material(vendor_id: str, material_id: str = "MAT-SEMI-1") -> dict[str, Any] | None:
    row = fetch_one(
        """
        SELECT vm.*, v.name, v.country
        FROM vendor_materials vm
        JOIN vendors v ON v.vendor_id = vm.vendor_id
        WHERE vm.vendor_id = %s AND vm.material_id = %s
        """,
        (vendor_id, material_id),
    )
    if not row:
        return None
    row["unit_price"] = _num(row["unit_price"])
    if row.get("weekly_capacity") is not None:
        row["weekly_capacity"] = _num(row["weekly_capacity"])
    return row


def vendor_materials(material_id: str = "MAT-SEMI-1") -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT vm.*, v.name, v.country
        FROM vendor_materials vm
        JOIN vendors v ON v.vendor_id = vm.vendor_id
        WHERE vm.material_id = %s
        ORDER BY vm.is_alternate, vm.vendor_id
        """,
        (material_id,),
    )
    for row in rows:
        row["unit_price"] = _num(row["unit_price"])
        if row.get("weekly_capacity") is not None:
            row["weekly_capacity"] = _num(row["weekly_capacity"])
    return rows
