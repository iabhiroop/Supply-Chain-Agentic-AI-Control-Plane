"""Project operational SCM tables into the knowledge graph (read-only from ERP + twin)."""

from __future__ import annotations

from decimal import Decimal

from graphrag.db import scm_conn
from graphrag.graph_store import merge_edge, merge_node
from graphrag.ingest import ingest_document


def project_operational() -> dict[str, int]:
    counts = {"vendors": 0, "materials": 0, "pos": 0, "twin_pos": 0, "edges": 0}
    with scm_conn() as conn:
        vendors = conn.execute("SELECT vendor_id, name, country FROM vendors").fetchall() if _table(conn, "vendors") else []
        materials = conn.execute("SELECT material_id, description FROM materials").fetchall() if _table(conn, "materials") else []
        plants = conn.execute("SELECT plant_id, name, country FROM plants").fetchall() if _table(conn, "plants") else []
        pos = conn.execute(
            "SELECT po_id, vendor_id, material_id, plant_id, quantity FROM exec_purchase_orders"
        ).fetchall() if _table(conn, "exec_purchase_orders") else []
        prs = conn.execute(
            "SELECT pr_id, material_id, plant_id, quantity FROM purchase_requisitions"
        ).fetchall() if _table(conn, "purchase_requisitions") else []
        vm = conn.execute(
            """
            SELECT vendor_id, material_id, lead_time_days, unit_price, is_alternate
              FROM vendor_materials
            """
        ).fetchall() if _table(conn, "vendor_materials") else []
        inventory = conn.execute(
            """
            SELECT plant_id, material_id, on_hand, daily_demand, days_of_cover
              FROM inventory_positions
            """
        ).fetchall() if _table(conn, "inventory_positions") else []
        demand = conn.execute(
            """
            SELECT plant_id, material_id, qty, horizon_days
              FROM independent_demand
            """
        ).fetchall() if _table(conn, "independent_demand") else []
        twin_pos = conn.execute(
            """
            SELECT po_id, vendor_id, material_id, plant_id, quantity, reallocatable_qty, status
              FROM twin_purchase_orders
            """
        ).fetchall() if _table(conn, "twin_purchase_orders") else []

    for v in vendors:
        sid = merge_node("Supplier", v["vendor_id"], v.get("name"), {"country": v.get("country")})
        counts["vendors"] += 1
        country = (v.get("country") or "").strip()
        if country:
            rid = merge_node("Region", country, country)
            merge_edge("LOCATED_IN", sid, rid)
            counts["edges"] += 1
    for m in materials:
        merge_node("Material", m["material_id"], m.get("description") or m["material_id"])
        counts["materials"] += 1
    for p in plants:
        props = {"country": p.get("country")}
        merge_node("Site", p["plant_id"], p.get("name") or p["plant_id"], props)
        merge_node("Warehouse", p["plant_id"], p.get("name") or p["plant_id"], props)
        country = (p.get("country") or "").strip()
        if country:
            merge_edge(
                "LOCATED_IN",
                merge_node("Site", p["plant_id"], p.get("name") or p["plant_id"]),
                merge_node("Region", country, country),
            )
            counts["edges"] += 1

    for row in vm:
        sid = merge_node("Supplier", row["vendor_id"])
        mid = merge_node("Material", row["material_id"])
        merge_edge("SUPPLIES", sid, mid)
        counts["edges"] += 1
        if row.get("is_alternate"):
            merge_edge("ALTERNATE_FOR", sid, mid)
            counts["edges"] += 1

    for row in inventory:
        plant_id = row["plant_id"]
        material_id = row["material_id"]
        cover = _num(row.get("days_of_cover"))
        site = merge_node(
            "Site",
            plant_id,
            properties={
                "days_of_cover": cover,
                "material_id": material_id,
                "on_hand": row.get("on_hand"),
            },
        )
        mid = merge_node("Material", material_id)
        merge_edge("USES", site, mid)
        merge_edge("STORED_AT", mid, site)
        counts["edges"] += 2

    for row in pos:
        _project_po(
            counts,
            po_id=row["po_id"],
            vendor_id=row.get("vendor_id"),
            material_id=row.get("material_id"),
            plant_id=row.get("plant_id"),
            quantity=row.get("quantity"),
        )
        counts["pos"] += 1
    for row in twin_pos:
        _project_po(
            counts,
            po_id=row["po_id"],
            vendor_id=row.get("vendor_id"),
            material_id=row.get("material_id"),
            plant_id=row.get("plant_id"),
            quantity=row.get("quantity"),
            extra={"status": row.get("status"), "reallocatable_qty": row.get("reallocatable_qty")},
        )
        counts["twin_pos"] += 1
    for pr in prs:
        pr_n = merge_node("Order", pr["pr_id"], pr["pr_id"])
        if pr.get("material_id"):
            merge_edge("CONTAINS", pr_n, merge_node("Material", pr["material_id"]))
        if pr.get("plant_id"):
            merge_edge("STORED_AT", pr_n, merge_node("Site", pr["plant_id"]))
        counts["edges"] += 1

    merge_node("Carrier", "Speedy Express", "Speedy Express")
    pol = merge_node("Policy", "DEFAULT", "P2P execution policy", {"price_deviation_pct": 5, "date_slip_days": 5})
    sla = merge_node("SLA", "VEND-A-MAT-11", "VEND-A MAT-11 5-day lead", {"lead_days": 5})
    merge_edge("GOVERNED_BY", merge_node("Supplier", "VEND-A"), pol)
    merge_edge("GOVERNED_BY", merge_node("Supplier", "VEND-A"), sla)
    merge_node("Contract", "C-VEND-A-2026", "Asturias Dairy Coop contract")
    merge_edge("ALTERNATE_FOR", merge_node("Supplier", "VEND-B"), merge_node("Material", "MAT-11"))
    merge_edge("ALTERNATE_FOR", merge_node("Supplier", "VEND-C"), merge_node("Material", "MAT-11"))
    if any(r.get("vendor_id") == "VEND-ABC" for r in vm):
        merge_node("Contract", "C-ABC-2026", "Taiwan Semiconductor Components contract")
        merge_edge("GOVERNED_BY", merge_node("Supplier", "VEND-ABC"), merge_node("Contract", "C-ABC-2026"))
    if any(r.get("is_alternate") and r.get("vendor_id") == "VEND-XYZ" for r in vm):
        merge_node("Contract", "C-XYZ-2026", "Penang Alternate Silicon contract")
        merge_edge("ALTERNATE_FOR", merge_node("Supplier", "VEND-XYZ"), merge_node("Material", "MAT-SEMI-1"))

    _ingest_twin_facts(vendors, plants, vm, inventory, demand, twin_pos)
    return counts


def _project_po(counts: dict, *, po_id, vendor_id, material_id, plant_id, quantity, extra: dict | None = None) -> None:
    props = {"quantity": quantity, **(extra or {})}
    po_n = merge_node("Order", po_id, po_id, props)
    if vendor_id:
        merge_edge("ORDERED_BY", po_n, merge_node("Supplier", vendor_id))
        merge_edge("SUPPLIES", merge_node("Supplier", vendor_id), merge_node("Material", material_id or "UNKNOWN"))
        counts["edges"] += 2
    if material_id:
        merge_edge("CONTAINS", po_n, merge_node("Material", material_id))
        counts["edges"] += 1
    if plant_id:
        merge_edge("STORED_AT", po_n, merge_node("Site", plant_id))
        counts["edges"] += 1
        if vendor_id:
            merge_edge("AFFECTED_BY", merge_node("Site", plant_id), merge_node("Supplier", vendor_id))
            counts["edges"] += 1


def _ingest_twin_facts(vendors, plants, vm, inventory, demand, twin_pos) -> None:
    """Overlay inventory/demand/PO facts as a citation-bearing document (not invented by the LLM)."""
    if not inventory and not twin_pos:
        return
    plant_name = {p["plant_id"]: p.get("name") or p["plant_id"] for p in plants}
    lines = [
        "# Twin disruption facts (projected from Digital Twin)",
        "",
        "Source of record: scm.inventory_positions, independent_demand, twin_purchase_orders, vendor_materials.",
        "",
    ]
    abc = next((v for v in vendors if v["vendor_id"] == "VEND-ABC"), None)
    if abc:
        lines.append(
            f"Supplier **VEND-ABC** ({abc.get('name')}) is LOCATED_IN **{abc.get('country')}** "
            "and SUPPLIES **MAT-SEMI-1**."
        )
    xyz = next((r for r in vm if r["vendor_id"] == "VEND-XYZ"), None)
    if xyz:
        lines.append(
            f"Supplier **VEND-XYZ** is ALTERNATE_FOR MAT-SEMI-1 "
            f"(lead {xyz.get('lead_time_days')} days, unit price {xyz.get('unit_price')})."
        )
    lines.append("")
    lines.append("## Plants using MAT-SEMI-1 (days of cover)")
    for row in inventory:
        pid = row["plant_id"]
        cover = _num(row.get("days_of_cover"))
        lines.append(
            f"- **{pid}** ({plant_name.get(pid, pid)}): on-hand {row.get('on_hand')}, "
            f"days_of_cover **{cover}**."
        )
    dem = {(d["plant_id"], d["material_id"]): d for d in demand}
    lines.append("")
    lines.append("## Demand vs cover")
    for row in inventory:
        d = dem.get((row["plant_id"], row["material_id"]))
        if not d:
            continue
        cover = _num(row.get("days_of_cover"))
        hz = d.get("horizon_days")
        lines.append(
            f"- {row['plant_id']}: demand horizon {hz} days vs cover {cover} "
            f"({'SHORT' if cover is not None and hz and hz > cover else 'ok'})."
        )
    lines.append("")
    lines.append("## Open twin purchase orders")
    for po in twin_pos:
        lines.append(
            f"- **{po['po_id']}** vendor {po['vendor_id']} material {po['material_id']} "
            f"plant {po['plant_id']} qty {po['quantity']} status {po.get('status')} "
            f"reallocatable {po.get('reallocatable_qty')}."
        )
    ingest_document(
        source_uri="twin://disruption-facts",
        text="\n".join(lines),
        title="Twin disruption facts",
        doc_type="md",
        metadata={"dataset": "twin-disruption-v1"},
    )


def _num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'scm' AND table_name = %s LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)
