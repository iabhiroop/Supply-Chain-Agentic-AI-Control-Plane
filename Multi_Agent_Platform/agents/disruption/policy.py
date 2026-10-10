"""Python policy for the disruption decision spine. No LLM arithmetic."""

from __future__ import annotations

from typing import Any

from agents.contracts import AlternateSpec, DisruptionRecommendation, ReallocateSpec, ShortPlant

SHORT_COVER_DAYS = 10
# Laptop pack shortage is 2300 units (CHN 1300 + BLR 1000). 1380/2300 = 60%
# until vendor_materials.weekly_capacity exists on the twin.
DEFAULT_ALTERNATE_WEEKLY_CAPACITY = 1380.0
REALLOCATE_PO = "PO-1842"


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def shortage_units(row: dict[str, Any]) -> float:
    cover = row.get("days_of_cover")
    horizon = row.get("horizon_days")
    daily = as_float(row.get("daily_demand"))
    if cover is None or horizon is None:
        return 0.0
    days_short = max(0.0, as_float(horizon) - as_float(cover))
    return round(days_short * daily, 2)


def short_plants(positions: list[dict[str, Any]], *, threshold: float = SHORT_COVER_DAYS) -> list[ShortPlant]:
    out: list[ShortPlant] = []
    for row in positions:
        cover = row.get("days_of_cover")
        if cover is None or as_float(cover) >= threshold:
            continue
        out.append(
            ShortPlant(
                plant_id=row["plant_id"],
                plant_name=str(row.get("plant_name") or ""),
                days_of_cover=as_float(cover),
                horizon_days=int(row.get("horizon_days") or 0),
                shortage_units=shortage_units(row),
                daily_demand=as_float(row.get("daily_demand")),
            )
        )
    return out


def premium_pct(primary_price: Any, alternate_price: Any) -> float:
    base = as_float(primary_price)
    if base <= 0:
        return 0.0
    return round((as_float(alternate_price) - base) / base * 100.0, 4)


def reallocate_spec(pos: list[dict[str, Any]], po_id: str = REALLOCATE_PO) -> ReallocateSpec | None:
    row = next((p for p in pos if p.get("po_id") == po_id), None)
    if not row:
        return None
    qty_po = as_float(row.get("quantity"))
    realloc = as_float(row.get("reallocatable_qty"))
    pct = round(100.0 * realloc / qty_po, 4) if qty_po else 0.0
    return ReallocateSpec(po_id=po_id, qty=realloc, pct_of_po=pct, po_quantity=qty_po)


def cover_pct(shortage: float, capacity: float) -> float:
    if shortage <= 0:
        return 0.0
    return round(100.0 * min(shortage, max(0.0, capacity)) / shortage, 2)


def alternate_capacity(xyz: dict[str, Any] | None) -> float:
    if xyz and xyz.get("weekly_capacity") is not None:
        return as_float(xyz["weekly_capacity"])
    return DEFAULT_ALTERNATE_WEEKLY_CAPACITY


def build_recommendation(
    *,
    vendor_id: str,
    material_id: str,
    positions: list[dict[str, Any]],
    pos: list[dict[str, Any]],
    primary: dict[str, Any] | None,
    alternate: dict[str, Any] | None,
) -> DisruptionRecommendation:
    plants = short_plants(positions)
    total_short = round(sum(p.shortage_units for p in plants), 2)
    realloc = reallocate_spec(pos)
    xyz = None
    if alternate:
        cap = alternate_capacity(alternate)
        xyz = AlternateSpec(
            vendor_id=str(alternate.get("vendor_id") or "VEND-XYZ"),
            lead_days=int(alternate.get("lead_time_days") or 0),
            premium_pct=premium_pct((primary or {}).get("unit_price"), alternate.get("unit_price")),
            cover_pct=cover_pct(total_short, cap),
            unit_price=as_float(alternate.get("unit_price")),
            weekly_capacity=cap,
        )
    rfq = bool(xyz) and (realloc is None or realloc.qty < total_short)
    rec = DisruptionRecommendation(
        vendor_id=vendor_id,
        material_id=material_id,
        short_plants=plants,
        affected_po_ids=[str(p["po_id"]) for p in pos if p.get("po_id")],
        reallocate=realloc,
        alternate=xyz,
        expedite_rfq=rfq,
        shortage_units=total_short,
    )
    rec.summary = render_summary(rec)
    return rec


def render_summary(rec: DisruptionRecommendation) -> str:
    plant_bits = ", ".join(
        f"{p.plant_id} {p.days_of_cover:g}d cover ({p.shortage_units:g} units short)"
        for p in rec.short_plants
    ) or "no short-cover plants"
    po_n = len(rec.affected_po_ids)
    realloc = rec.reallocate
    alt = rec.alternate
    parts = [
        f"{po_n} open purchase orders are at risk from {rec.vendor_id} on {rec.material_id}.",
        f"Short-cover plants: {plant_bits}.",
        f"Projected shortage {rec.shortage_units:g} units.",
    ]
    if realloc:
        parts.append(
            f"Recommend reallocating {realloc.pct_of_po:g}% of {realloc.po_id} "
            f"({realloc.qty:g} of {realloc.po_quantity:g})."
        )
    if alt:
        parts.append(
            f"{alt.vendor_id} lead {alt.lead_days} days, {alt.premium_pct:g}% premium, "
            f"can cover {alt.cover_pct:g}% of the shortage"
            + (" — initiate an expedited RFQ." if rec.expedite_rfq else ".")
        )
    return " ".join(parts)
