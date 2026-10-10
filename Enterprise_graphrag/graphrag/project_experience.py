"""Project ERP memory tables and seed historical playbook incidents into the graph."""

from __future__ import annotations

from graphrag.db import scm_conn
from graphrag.graph_store import merge_edge, merge_node


def project_experience() -> dict[str, int]:
    n = 0
    n += _seed_playbook()
    n += _project_memory_tables()
    return {"experience_edges": n}


def _seed_playbook() -> int:
    vend_a = merge_node("Supplier", "VEND-A", "Asturias Dairy Coop")
    vend_c = merge_node("Supplier", "VEND-C", "Premium Iberia")
    mat = merge_node("Material", "MAT-11", "Queso Cabrales")
    site = merge_node("Site", "WH-FR-01", "Le Havre DC")
    pol = merge_node("Policy", "DEFAULT", "P2P execution policy", {"price_deviation_pct": 5, "date_slip_days": 5})
    carrier = merge_node("Carrier", "Speedy Express", "Speedy Express")
    region = merge_node("Region", "Spain", "Spain")
    po = merge_node("Order", "PO-1001", "PO-1001")
    pr1 = merge_node("Order", "PR-1001", "PR-1001")
    pr2 = merge_node("Order", "PR-1002", "PR-1002")

    merge_edge("LOCATED_IN", carrier, region)
    merge_edge("SHIPPED_VIA", po, carrier)
    merge_edge("SHIPPED_VIA", pr2, carrier)
    merge_edge("STORED_AT", po, site)
    merge_edge("SUPPLIES", vend_a, mat)

    inc_delay = merge_node(
        "Incident",
        "INC-DELAY-VEND-A",
        "VEND-A 3-day ready-date slip",
        {"root_cause": "supplier_delay", "recovery": "rebook Speedy Express", "effective": True, "slip_days": 3},
    )
    dec_rebook = merge_node(
        "Decision",
        "DEC-REBOOK-SPEEDY",
        "Rebook Speedy Express within date_slip_days",
        {"action": "rebook", "effective": True, "carrier": "Speedy Express"},
    )
    merge_edge("AFFECTED_BY", inc_delay, vend_a)
    merge_edge("AFFECTED_BY", inc_delay, pr2)
    merge_edge("AFFECTED_BY", inc_delay, site)
    merge_edge("RESOLVED_BY", inc_delay, dec_rebook)
    merge_edge("LEARNED_FROM", vend_a, inc_delay)
    merge_edge("LEARNED_FROM", pr2, inc_delay)

    inc_long = merge_node(
        "Incident",
        "INC-SLIP-14D-VEND-A",
        "VEND-A two-week lead-time slip",
        {"root_cause": "supplier_delay", "recovery": "AWAITING_HUMAN", "effective": True, "slip_days": 14},
    )
    dec_hitl = merge_node(
        "Decision",
        "DEC-HITL-SLIP",
        "Hold for human; slip exceeds 5-day policy",
        {"action": "AWAITING_HUMAN", "effective": True},
    )
    merge_edge("AFFECTED_BY", inc_long, vend_a)
    merge_edge("AFFECTED_BY", inc_long, site)
    merge_edge("BREACHES", inc_long, pol)
    merge_edge("RESOLVED_BY", inc_long, dec_hitl)
    merge_edge("LEARNED_FROM", vend_a, inc_long)

    inc_price = merge_node(
        "Incident",
        "INC-PRICE-VEND-C",
        "VEND-C quote +12% rejected",
        {"root_cause": "price_policy", "recovery": "reject bid", "effective": True},
    )
    dec_reject = merge_node(
        "Decision",
        "DEC-REJECT-VEND-C",
        "Reject VEND-C 11.20 as illegal vs 5% cap",
        {"action": "reject_bid", "effective": True},
    )
    merge_edge("AFFECTED_BY", inc_price, vend_c)
    merge_edge("AFFECTED_BY", inc_price, mat)
    merge_edge("BREACHES", inc_price, pol)
    merge_edge("RESOLVED_BY", inc_price, dec_reject)
    merge_edge("LEARNED_FROM", vend_c, inc_price)

    inc_match = merge_node(
        "Incident",
        "INC-MATCH-PR-1001",
        "Four-way match SETTLED on PO-1001",
        {"root_cause": "none", "recovery": "SETTLED", "effective": True},
    )
    dec_settle = merge_node(
        "Decision",
        "DEC-SETTLED-PO-1001",
        "SETTLED four-way match",
        {"action": "SETTLED", "effective": True},
    )
    merge_edge("AFFECTED_BY", inc_match, po)
    merge_edge("AFFECTED_BY", inc_match, pr1)
    merge_edge("RESOLVED_BY", inc_match, dec_settle)
    merge_edge("LEARNED_FROM", po, inc_match)
    merge_edge("ORDERED_BY", po, vend_a)
    return 6


def _project_memory_tables() -> int:
    count = 0
    with scm_conn() as conn:
        if not _table(conn, "supplier_memory"):
            return 0
        suppliers = conn.execute(
            """
            SELECT vendor_id, reliability_note, response_hours
              FROM supplier_memory ORDER BY created_at DESC LIMIT 50
            """
        ).fetchall()
        lanes = conn.execute(
            """
            SELECT region, carrier_name, transit_days
              FROM lane_memory ORDER BY created_at DESC LIMIT 50
            """
        ).fetchall() if _table(conn, "lane_memory") else []
        commercials = conn.execute(
            """
            SELECT vendor_id, material_id, quoted_price, paid_price
              FROM commercial_memory ORDER BY created_at DESC LIMIT 50
            """
        ).fetchall() if _table(conn, "commercial_memory") else []
        exceptions = conn.execute(
            """
            SELECT transaction_id, root_cause, recovery_action, effective
              FROM exception_memory ORDER BY created_at DESC LIMIT 50
            """
        ).fetchall() if _table(conn, "exception_memory") else []

    for row in suppliers:
        sid = merge_node("Supplier", row["vendor_id"], row["vendor_id"])
        inc = merge_node(
            "Incident",
            f"MEM-SUP-{row['vendor_id']}",
            row.get("reliability_note") or "supplier memory",
            {"response_hours": float(row["response_hours"] or 0), "source": "supplier_memory"},
        )
        merge_edge("LEARNED_FROM", sid, inc)
        merge_edge("AFFECTED_BY", inc, sid)
        count += 1
    for row in lanes:
        if not row.get("carrier_name"):
            continue
        c = merge_node("Carrier", row["carrier_name"], row["carrier_name"], {"transit_days": row.get("transit_days")})
        r = merge_node("Region", row["region"] or "Spain", row["region"] or "Spain")
        merge_edge("LOCATED_IN", c, r)
        count += 1
    for row in commercials:
        v = merge_node("Supplier", row["vendor_id"])
        m = merge_node("Material", row["material_id"])
        merge_edge("SUPPLIES", v, m, provenance={"quoted": str(row.get("quoted_price")), "paid": str(row.get("paid_price"))})
        count += 1
    for row in exceptions:
        key = f"MEM-EXC-{str(row.get('transaction_id') or '')[:8]}"
        inc = merge_node(
            "Incident",
            key,
            row.get("root_cause") or "exception",
            {
                "recovery": row.get("recovery_action"),
                "effective": bool(row.get("effective")),
                "source": "exception_memory",
            },
        )
        dec = merge_node(
            "Decision",
            f"DEC-{key}",
            row.get("recovery_action") or "recovery",
            {"action": row.get("recovery_action"), "effective": bool(row.get("effective"))},
        )
        merge_edge("RESOLVED_BY", inc, dec)
        count += 1
    return count


def _table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'scm' AND table_name = %s LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)
