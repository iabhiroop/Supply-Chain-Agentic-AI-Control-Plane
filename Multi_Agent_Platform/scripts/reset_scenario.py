"""Reset the reference disruption scenario to its pristine state.

Agent runs and smoke tests mutate business data, so repeated runs need a fast
way back to a known starting point. This restores just the scenario rows in a
couple of seconds instead of rebuilding the whole database (which reloads 609k
order lines).

Reproducibility matters beyond convenience: Layer 4 cannot compare two runs of
the same workflow unless they start from identical state.

Usage:
    python scripts/reset_scenario.py
    python scripts/reset_scenario.py --keep-history   # leave incidents/actions
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.config.settings import settings  # noqa: E402

SQLITE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "northwind.db"
)

ORDER_ID = 10248
PRODUCT_ID = 11
BIN_ID = "A1-01"
SYSTEM_QTY = 20
ORIGINAL_CARRIER_ID = 3  # Federal Shipping — the carrier that drops the load


def original_units_in_stock(product_id: int) -> int:
    """Read the untouched on-hand figure from the Northwind source of truth."""
    lite = sqlite3.connect(SQLITE_PATH)
    row = lite.execute(
        "SELECT UnitsInStock FROM Products WHERE ProductID = ?", (product_id,)
    ).fetchone()
    lite.close()
    return int(row[0] or 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-history", action="store_true",
                        help="keep incidents, agent runs, actions and communications")
    args = parser.parse_args()

    today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    units = original_units_in_stock(PRODUCT_ID)

    with psycopg.connect(settings.pg_dsn(), row_factory=dict_row,
                         options="-c search_path=scm,public") as conn:
        # --- inventory ---
        conn.execute(
            "UPDATE bin_stock SET quantity = %s, updated_at = now() "
            "WHERE product_id = %s AND bin_id = %s",
            (SYSTEM_QTY, PRODUCT_ID, BIN_ID),
        )
        conn.execute(
            "UPDATE bin_stock SET quantity = 15, updated_at = now() "
            "WHERE product_id = %s AND bin_id = 'C1-01'",
            (PRODUCT_ID,),
        )
        removed = conn.execute(
            "DELETE FROM stock_ledger WHERE product_id = %s AND transaction_type IN ('ADJUSTMENT', 'TRANSFER') "
            "RETURNING transaction_id",
            (PRODUCT_ID,),
        ).fetchall()
        conn.execute(
            "UPDATE products SET units_in_stock = %s WHERE product_id = %s", (units, PRODUCT_ID)
        )

        # --- shipment: back to the delayed, dropped-load state ---
        conn.execute(
            """
            UPDATE shipments
            SET carrier_id = %s, status = 'DELAYED', service_level = 'STANDARD',
                promised_delivery = %s, estimated_delivery = %s,
                freight_cost = 15.50, updated_at = now()
            WHERE order_id = %s
            """,
            (ORIGINAL_CARRIER_ID, today + timedelta(days=2), today + timedelta(days=7), ORDER_ID),
        )

        # --- platform history ---
        if not args.keep_history:
            for table in ("goods_receipts", "detected_signals", "communications",
                          "actions", "agent_runs", "incident_events",
                          "idempotency_keys", "incidents"):
                conn.execute(f"DELETE FROM {table}")

        from database.seed_dashboard_scenarios import seed_demo_scenarios
        seed_demo_scenarios(conn)

        stock = conn.execute(
            "SELECT quantity FROM bin_stock WHERE product_id=%s AND bin_id=%s",
            (PRODUCT_ID, BIN_ID),
        ).fetchone()
        ship = conn.execute(
            """
            SELECT c.carrier_name, s.status, s.service_level,
                   EXTRACT(DAY FROM (s.estimated_delivery - s.promised_delivery))::int AS days_late
            FROM shipments s JOIN carriers c ON c.carrier_id = s.carrier_id
            WHERE s.order_id = %s
            """,
            (ORDER_ID,),
        ).fetchone()
        conn.commit()

    print("Reference scenario reset:")
    print(f"  bin {BIN_ID} product {PRODUCT_ID}: system quantity {stock['quantity']} "
          f"(physically empty — the discrepancy to find)")
    print(f"  product {PRODUCT_ID} on hand: {units}")
    print(f"  removed {len(removed)} adjustment ledger entr{'y' if len(removed) == 1 else 'ies'}")
    print(f"  order {ORDER_ID}: {ship['carrier_name']} / {ship['status']} / "
          f"{ship['service_level']} / {ship['days_late']} days late")
    if not args.keep_history:
        print("  cleared incidents, agent runs, actions, communications")
    print("  re-applied dashboard demo shipments and extra bins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
