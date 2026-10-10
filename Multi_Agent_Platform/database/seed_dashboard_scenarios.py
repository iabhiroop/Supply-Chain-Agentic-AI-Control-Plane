"""Extra dashboard / copilot demo rows on top of the 10248 reference scenario.

Safe to re-run. Does not rebuild Northwind. Uses existing orders, bins, and carriers.

These cases are sized for the *current* four agents (stock, tendering, dispatching,
issue resolution) — not the later sourcing / root-cause agents.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

# Northwind orders we hang TMS/WMS exceptions on.
ORDER_CARRIER_REJECTED = 10251   # VICTE, France — stock OK, Federal has no capacity
ORDER_SHIPMENT_DELAYED = 10249   # TOMSP, Germany — late, alternatives exist
ORDER_SECOND_MISMATCH = 10252    # SUPRD, France — empty pick bin, different SKU
ORDER_COMMS_ONLY = 10250         # HANAR, Brazil — in transit and late; SLA notify

BIN_SECOND_EMPTY = "B1-02"
BIN_ALT_STOCK = "C1-01"          # extra on-hand for product 11 (substitution question)
PRODUCT_SECOND = 20              # Chang — typical 10252 line; verified at runtime


def _today() -> datetime:
    return datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)


def _first_line(cur, order_id: int) -> tuple[int, str] | None:
    row = cur.execute(
        """
        SELECT od.product_id, p.product_name
        FROM scm.order_details od
        JOIN scm.products p ON p.product_id = od.product_id
        WHERE od.order_id = %s
        ORDER BY od.product_id
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return int(row["product_id"]), str(row["product_name"])
    return int(row[0]), str(row[1])


def seed_demo_scenarios(conn: psycopg.Connection) -> None:
    cur = conn.cursor()
    today = _today()

    # Alternate location for Queso Cabrales so copilot can answer "stock elsewhere?"
    cur.execute(
        """
        INSERT INTO scm.bin_stock (bin_id, product_id, quantity)
        VALUES (%s, 11, 15)
        ON CONFLICT (bin_id, product_id) DO UPDATE SET quantity = 15, updated_at = now()
        """,
        (BIN_ALT_STOCK,),
    )
    cur.execute(
        """
        INSERT INTO scm.stock_ledger (product_id, bin_id, transaction_type, quantity, reason, reference)
        SELECT 11, %s, 'RECEIPT', 15, 'Overspill from A-aisle', 'GR-DEMO-11'
        WHERE NOT EXISTS (
            SELECT 1 FROM scm.stock_ledger
            WHERE product_id = 11 AND bin_id = %s AND reference = 'GR-DEMO-11'
        )
        """,
        (BIN_ALT_STOCK, BIN_ALT_STOCK),
    )

    line = _first_line(cur, ORDER_SECOND_MISMATCH)
    if line:
        pid, _name = line
        cur.execute(
            """
            INSERT INTO scm.bin_stock (bin_id, product_id, quantity)
            VALUES (%s, %s, 0)
            ON CONFLICT (bin_id, product_id) DO UPDATE SET quantity = 0, updated_at = now()
            """,
            (BIN_SECOND_EMPTY, pid),
        )
        cur.execute(
            """
            INSERT INTO scm.stock_ledger (product_id, bin_id, transaction_type, quantity, reason, reference)
            SELECT %s, %s, 'ISSUE', -12, 'Unrecorded pick — suspected mis-issue', 'GI-DEMO-MISS'
            WHERE NOT EXISTS (
                SELECT 1 FROM scm.stock_ledger WHERE reference = 'GI-DEMO-MISS'
            )
            """,
            (pid, BIN_SECOND_EMPTY),
        )

    # Ghost empty bin (no open order) — floor exception for copilot-only investigation.
    cur.execute(
        """
        INSERT INTO scm.bin_stock (bin_id, product_id, quantity)
        VALUES ('B1-06', 41, 0)
        ON CONFLICT (bin_id, product_id) DO UPDATE SET quantity = 0, updated_at = now()
        """
    )

    extra_rates = [
        (4, "Germany", 10.00, 30.00, 4, 2, True),  # already in base seed; ON CONFLICT skip
        (2, "Germany", 16.00, 42.00, 5, 2, True),
        (1, "Brazil", 22.00, 55.00, 8, 4, True),
        (2, "Brazil", 20.00, 48.00, 7, 4, True),
        (5, "Brazil", 40.00, 75.00, 3, 2, True),
    ]
    for row in extra_rates:
        cur.execute(
            """
            INSERT INTO scm.carrier_rates (
                carrier_id, region, base_rate, expedited_rate,
                base_transit_days, expedited_transit_days, capacity_available
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (carrier_id, region) DO NOTHING
            """,
            row,
        )

    def upsert_shipment(order_id, carrier_id, status, country, service, promised, estimated, cost):
        exists = cur.execute(
            "SELECT shipment_id FROM scm.shipments WHERE order_id = %s LIMIT 1", (order_id,)
        ).fetchone()
        if exists:
            cur.execute(
                """
                UPDATE scm.shipments
                SET carrier_id = %s, status = %s, origin_warehouse_id = 'WH-FR-01',
                    destination_country = %s, service_level = %s,
                    promised_delivery = %s, estimated_delivery = %s,
                    freight_cost = %s, updated_at = now()
                WHERE order_id = %s
                """,
                (carrier_id, status, country, service, promised, estimated, cost, order_id),
            )
        else:
            cur.execute(
                """
                INSERT INTO scm.shipments (
                    order_id, carrier_id, status, origin_warehouse_id, destination_country,
                    service_level, promised_delivery, estimated_delivery, freight_cost
                ) VALUES (%s,%s,%s,'WH-FR-01',%s,%s,%s,%s,%s)
                """,
                (order_id, carrier_id, status, country, service, promised, estimated, cost),
            )

    # 10251: France, Federal (no capacity) — dropped load, stock is fine.
    upsert_shipment(
        ORDER_CARRIER_REJECTED, 3, "DELAYED", "France", "STANDARD",
        today + timedelta(days=2), today + timedelta(days=6), 15.50,
    )
    # 10249: Germany, United Package late — Euro Freight has capacity.
    upsert_shipment(
        ORDER_SHIPMENT_DELAYED, 2, "DELAYED", "Germany", "STANDARD",
        today + timedelta(days=3), today + timedelta(days=8), 18.00,
    )
    # 10252: France, still planned but pick will fail on empty B1-02.
    upsert_shipment(
        ORDER_SECOND_MISMATCH, 1, "PLANNED", "France", "STANDARD",
        today + timedelta(days=4), today + timedelta(days=4), 12.00,
    )
    # 10250: already rolling, late — Issue Resolution / SLA notify, no stock fix.
    upsert_shipment(
        ORDER_COMMS_ONLY, 2, "IN_TRANSIT", "Brazil", "STANDARD",
        today + timedelta(days=1), today + timedelta(days=5), 20.00,
    )

    seed_inbound_asn(conn)

    print(
        "  demo scenarios: 10251 dropped load, 10249 Germany delay, "
        "10252 empty B1-02, 10250 in-transit late, product 11 also in C1-01, ASN-1001 GR miss"
    )


def seed_inbound_asn(conn: psycopg.Connection) -> None:
    """Receiving bin + one ASN expected 20 / counted 0 for product 11's supplier."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO scm.storage_bins (bin_id, warehouse_id, zone, capacity)
        VALUES ('RCV-01', 'WH-FR-01', 'RECEIVING', 200)
        ON CONFLICT (bin_id) DO NOTHING
        """
    )
    row = cur.execute(
        "SELECT supplier_id FROM scm.products WHERE product_id = 11"
    ).fetchone()
    supplier_id = 1
    if row is not None:
        supplier_id = int(row["supplier_id"] if isinstance(row, dict) else row[0])
    cur.execute(
        """
        INSERT INTO scm.purchase_orders (po_id, supplier_id, status)
        VALUES ('PO-11', %s, 'OPEN')
        ON CONFLICT (po_id) DO UPDATE SET supplier_id = EXCLUDED.supplier_id
        """,
        (supplier_id,),
    )
    cur.execute(
        """
        INSERT INTO scm.purchase_order_lines (po_id, line_no, product_id, quantity)
        VALUES ('PO-11', 1, 11, 20)
        ON CONFLICT (po_id, line_no) DO UPDATE SET quantity = 20, product_id = 11
        """
    )
    cur.execute(
        """
        INSERT INTO scm.inbound_asns (asn_id, po_id, supplier_id, status, expected_bin_id)
        VALUES ('ASN-1001', 'PO-11', %s, 'IN_TRANSIT', 'RCV-01')
        ON CONFLICT (asn_id) DO UPDATE
          SET po_id = 'PO-11', supplier_id = EXCLUDED.supplier_id,
              status = 'IN_TRANSIT', expected_bin_id = 'RCV-01'
        """,
        (supplier_id,),
    )
    cur.execute(
        """
        INSERT INTO scm.inbound_asn_lines (asn_id, line_no, product_id, expected_qty, counted_qty)
        VALUES ('ASN-1001', 1, 11, 20, 0)
        ON CONFLICT (asn_id, line_no) DO UPDATE
          SET product_id = 11, expected_qty = 20, counted_qty = 0
        """
    )
    cur.execute("DELETE FROM scm.goods_receipts WHERE asn_id = 'ASN-1001'")


def main() -> None:
    from agents.config.settings import settings

    with psycopg.connect(settings.pg_dsn(), options="-c search_path=scm,public") as conn:
        seed_demo_scenarios(conn)
        seed_inbound_asn(conn)
        conn.commit()
    print("dashboard demo scenarios seeded.")


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
