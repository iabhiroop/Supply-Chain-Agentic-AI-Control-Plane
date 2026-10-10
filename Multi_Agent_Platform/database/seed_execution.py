"""Seed the P2P golden transaction PR-1001 and a delay-exception sibling."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Json

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

GOLDEN_TX = "aaaaaaaa-bbbb-cccc-dddd-000000000001"
DELAY_TX = "aaaaaaaa-bbbb-cccc-dddd-000000000002"
HITL_TX = "aaaaaaaa-bbbb-cccc-dddd-000000000003"


def _read(name: str) -> str:
    path = os.path.join(FIX, name)
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    return ""


def _purge_tx(cur, transaction_id: str, pr_id: str, *, full: bool = False) -> None:
    if full:
        cur.execute("DELETE FROM invoice_matches WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM freight_invoices WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM supplier_invoices WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM goods_receipts_exec WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM quotes WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM rfqs WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM supplier_memory WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM lane_memory WHERE transaction_id = %s", (transaction_id,))
        cur.execute("DELETE FROM commercial_memory WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM tracking_events WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM bookings WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM exec_purchase_orders WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM channel_messages WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM transaction_events WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM exception_memory WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM hitl_requests WHERE transaction_id = %s", (transaction_id,))
    cur.execute("DELETE FROM purchase_requisitions WHERE pr_id = %s", (pr_id,))
    cur.execute("DELETE FROM execution_transactions WHERE transaction_id = %s", (transaction_id,))


def _seed_golden(cur, need_by) -> None:
    _purge_tx(cur, GOLDEN_TX, "PR-1001", full=True)
    cur.execute(
        """
        INSERT INTO execution_transactions (transaction_id, pr_id, stage, status, context)
        VALUES (%s, 'PR-1001', 'INTENT', 'OPEN', '{}'::jsonb)
        """,
        (GOLDEN_TX,),
    )
    cur.execute(
        """
        INSERT INTO purchase_requisitions
            (pr_id, transaction_id, material_id, quantity, plant_id, cost_center, need_by, status)
        VALUES ('PR-1001', %s, 'MAT-11', 20, 'WH-FR-01', 'CC-100', %s, 'OPEN')
        """,
        (GOLDEN_TX, need_by),
    )
    cur.execute(
        """
        INSERT INTO transaction_events (transaction_id, event, detail)
        VALUES (%s, 'OPENED', %s)
        """,
        (GOLDEN_TX, Json({"pr_id": "PR-1001"})),
    )


def _seed_in_transit_delay(cur, tx_id, pr_id, po_id, booking_id, slip_days, need_by, body) -> None:
    _purge_tx(cur, tx_id, pr_id)
    cur.execute(
        """
        INSERT INTO execution_transactions (transaction_id, pr_id, stage, status, context)
        VALUES (%s, %s, 'IN_TRANSIT', 'EXCEPTION', %s)
        """,
        (tx_id, pr_id, Json({"exception": "supplier_delay", "slip_days": slip_days})),
    )
    cur.execute(
        """
        INSERT INTO purchase_requisitions
            (pr_id, transaction_id, material_id, quantity, plant_id, cost_center, need_by, status)
        VALUES (%s, %s, 'MAT-11', 20, 'WH-FR-01', 'CC-100', %s, 'ORDERED')
        """,
        (pr_id, tx_id, need_by),
    )
    cur.execute(
        """
        INSERT INTO exec_purchase_orders
            (po_id, transaction_id, vendor_id, material_id, quantity, unit_price, plant_id, cost_center, need_by, status)
        VALUES (%s, %s, 'VEND-A', 'MAT-11', 20, 10.20, 'WH-FR-01', 'CC-100', %s, 'ISSUED')
        """,
        (po_id, tx_id, need_by),
    )
    cur.execute(
        """
        INSERT INTO bookings (booking_id, transaction_id, po_id, carrier_id, carrier_name, freight_cost, eta, status)
        VALUES (%s, %s, %s, 1, 'Speedy Express', 85.00, %s, 'IN_TRANSIT')
        """,
        (booking_id, tx_id, po_id, need_by + timedelta(days=2)),
    )
    cur.execute(
        """
        INSERT INTO channel_messages (
            message_id, transaction_id, direction, channel, counterparty, subject, body, parsed
        ) VALUES (%s,%s,'IN','EMAIL','VEND-A',%s,%s,%s)
        """,
        (
            str(uuid.uuid4()),
            tx_id,
            f"Delay on {po_id}",
            body,
            Json({"type": "delay", "po_id": po_id, "slip_days": slip_days}),
        ),
    )


def seed_execution(conn: psycopg.Connection, jobs: tuple[str, ...] | None = None) -> None:
    selected = set(jobs or ("golden", "delay", "hitl"))
    cur = conn.cursor()
    need_by = date.today() + timedelta(days=14)

    cur.execute(
        """
        INSERT INTO plants (plant_id, name, country) VALUES ('WH-FR-01', 'Le Havre DC', 'France')
        ON CONFLICT (plant_id) DO UPDATE SET name = EXCLUDED.name
        """
    )
    cur.execute(
        """
        INSERT INTO gl_accounts (gl_account, name) VALUES ('4000', 'Inventory receipts')
        ON CONFLICT (gl_account) DO NOTHING
        """
    )
    cur.execute(
        """
        INSERT INTO cost_centers (cost_center, name, budget_usd)
        VALUES ('CC-100', 'Outbound replenishment', 2500)
        ON CONFLICT (cost_center) DO UPDATE SET budget_usd = 2500
        """
    )
    cur.execute(
        """
        INSERT INTO materials (material_id, description, uom, last_price, gl_account)
        VALUES ('MAT-11', 'Queso Cabrales', 'EA', 10.00, '4000')
        ON CONFLICT (material_id) DO UPDATE SET last_price = 10.00
        """
    )
    vendors = [
        ("VEND-A", "Asturias Dairy Coop", "sales@asturias.example", "Spain", 5),
        ("VEND-B", "Cantabria Foods", "bids@cantabria.example", "Spain", 8),
        ("VEND-C", "Premium Iberia", "quotes@premium-iberia.example", "Spain", 4),
    ]
    for vid, name, email, country, lead in vendors:
        cur.execute(
            """
            INSERT INTO vendors (vendor_id, name, email, country, incoterms, payment_terms, moq, lead_time_days)
            VALUES (%s,%s,%s,%s,'FOB','NET30',10,%s)
            ON CONFLICT (vendor_id) DO UPDATE SET email = EXCLUDED.email, lead_time_days = EXCLUDED.lead_time_days
            """,
            (vid, name, email, country, lead),
        )
    cur.execute(
        """
        INSERT INTO contracted_rates (plant_id, carrier_id, carrier_name, region, mode, rate_usd)
        VALUES ('WH-FR-01', 1, 'Speedy Express', 'Spain', 'ROAD', 85.00)
        ON CONFLICT (plant_id, carrier_id, region) DO UPDATE SET rate_usd = 85.00, carrier_name = EXCLUDED.carrier_name
        """
    )
    cur.execute(
        """
        INSERT INTO routing_guidelines (plant_id, region, incoterms, default_mode)
        VALUES ('WH-FR-01', 'Spain', 'FOB', 'ROAD')
        ON CONFLICT (plant_id, region) DO NOTHING
        """
    )
    cur.execute(
        """
        INSERT INTO execution_policy (policy_id, price_deviation_pct, date_slip_days, approval_amount_usd)
        VALUES ('DEFAULT', 5, 5, 5000)
        ON CONFLICT (policy_id) DO UPDATE
          SET price_deviation_pct = 5, date_slip_days = 5, approval_amount_usd = 5000
        """
    )
    cur.execute(
        """
        INSERT INTO approval_rules (rule_id, cost_center, min_amount, must_hitl)
        VALUES ('CC-100-HITL', 'CC-100', 5000, TRUE)
        ON CONFLICT (rule_id) DO UPDATE SET min_amount = 5000
        """
    )

    if "golden" in selected:
        _seed_golden(cur, need_by)
    if "delay" in selected:
        _seed_in_transit_delay(
            cur, DELAY_TX, "PR-1002", "PO-1002", "BK-1002", 3, need_by,
            "PO-1002 pickup delayed 3 days due to dock congestion at origin. "
            "New ready date is +3 days. — Asturias Dairy Coop",
        )
    if "hitl" in selected:
        _seed_in_transit_delay(
            cur, HITL_TX, "PR-1003", "PO-1003", "BK-1003", 14, need_by,
            "PO-1003 pickup delayed 14 days due to plant shutdown at origin. "
            "New ready date is +14 days. — Asturias Dairy Coop",
        )

    seeded = ", ".join(j for j in ("golden", "delay", "hitl") if j in selected)
    print(f"  execution seed: {seeded} (PR-1001 / PR-1002 / PR-1003 as selected)")


def main() -> None:
    sys.path.insert(0, os.path.dirname(HERE))
    from agents.config.settings import settings

    with psycopg.connect(settings.pg_dsn(), options="-c search_path=scm,public") as conn:
        from database.apply_erp_execution import apply
        apply(conn)
        exec_views = os.path.join(HERE, "chat_ro_execution.sql")
        if os.path.exists(exec_views):
            conn.execute(open(exec_views, encoding="utf-8").read())
        seed_execution(conn)
        conn.commit()
    print("execution scenario seeded.")


if __name__ == "__main__":
    main()
