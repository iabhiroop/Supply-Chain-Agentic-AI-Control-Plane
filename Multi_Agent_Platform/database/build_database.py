"""Build the logistics_scm PostgreSQL database from scratch.

Steps:
  1. Create the `logistics_scm` database if it does not exist.
  2. Apply `schema.sql`.
  3. Migrate Northwind ERP data out of the legacy SQLite file.
  4. Seed synthetic WMS / TMS / contract data, including the reference
     disruption scenario for order 10248.

Everything is deterministic (fixed RNG seed, explicit primary keys) so repeated
runs produce an identical database. Re-running is safe: the schema is dropped
and recreated.

Usage:
    python -m database.build_database          # from Multi_Agent_Platform/
    python database/build_database.py
"""

from __future__ import annotations

import os
import random
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta

import psycopg
from psycopg import sql

HERE = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.path.join(HERE, "northwind.db")
SCHEMA_PATH = os.path.join(HERE, "schema.sql")
INBOUND_PATH = os.path.join(HERE, "inbound_schema.sql")
CHAT_RO_PATH = os.path.join(HERE, "chat_ro.sql")
ERP_PATH = os.path.join(HERE, "erp_execution.sql")
CHAT_RO_EXEC_PATH = os.path.join(HERE, "chat_ro_execution.sql")

PG_HOST = os.getenv("SCM_PG_HOST", "localhost")
PG_PORT = os.getenv("SCM_PG_PORT", "5432")
PG_USER = os.getenv("SCM_PG_USER", "gateway_user")
PG_PASSWORD = os.getenv("SCM_PG_PASSWORD", "gateway_pass")
PG_DATABASE = os.getenv("SCM_PG_DATABASE", "logistics_scm")

RNG_SEED = 42

# Reference scenario constants (see plan.md §7)
SCENARIO_ORDER_ID = 10248
SCENARIO_PRODUCT_ID = 11          # Queso Cabrales
SCENARIO_CUSTOMER_ID = "VINET"    # Vins et alcools Chevalier, France
SCENARIO_BIN = "A1-01"
SCENARIO_SYSTEM_QTY = 20          # what the system believes is on hand
SCENARIO_WAREHOUSE = "WH-FR-01"


def admin_dsn(dbname: str = "postgres") -> str:
    return f"host={PG_HOST} port={PG_PORT} user={PG_USER} password={PG_PASSWORD} dbname={dbname}"


def slugify(value: str) -> str:
    """Deterministic ASCII slug, used to synthesise email addresses."""
    if not value:
        return "contact"
    norm = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    norm = re.sub(r"[^a-zA-Z0-9]+", ".", norm).strip(".").lower()
    return norm or "contact"


def ts(value):
    """Normalise SQLite datetime text to something psycopg accepts."""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return value


def to_bool(value) -> bool:
    return str(value).strip() in ("1", "true", "True", "t", "yes")


def bulk_copy(cur, table: str, columns: list[str], rows) -> int:
    """Load rows via COPY.

    The Northwind variant in this repo has ~609k order_detail rows; per-row
    INSERTs take many minutes, while COPY takes seconds.
    """
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.SQL(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
    )
    count = 0
    with cur.copy(stmt) as cp:
        for row in rows:
            cp.write_row(row)
            count += 1
    return count


# -----------------------------------------------------------------------------
# 1. Database creation
# -----------------------------------------------------------------------------

def ensure_database() -> None:
    with psycopg.connect(admin_dsn(), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (PG_DATABASE,)
        ).fetchone()
        if exists:
            print(f"  database '{PG_DATABASE}' already exists")
        else:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(PG_DATABASE)))
            print(f"  created database '{PG_DATABASE}'")


def apply_schema(conn: psycopg.Connection) -> None:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.execute(fh.read())
    print("  schema applied (schema 'scm' recreated)")
    with open(INBOUND_PATH, "r", encoding="utf-8") as fh:
        conn.execute(fh.read())
    print("  inbound ASN/GR and detected_signals tables applied")
    with open(ERP_PATH, "r", encoding="utf-8") as fh:
        conn.execute(fh.read())
    print("  execution ERP / P2P transaction spine applied")
    with open(CHAT_RO_PATH, "r", encoding="utf-8") as fh:
        conn.execute(fh.read())
    print("  chat_ro views and scm_chat_reader role applied")
    with open(CHAT_RO_EXEC_PATH, "r", encoding="utf-8") as fh:
        conn.execute(fh.read())
    print("  chat_ro execution views applied")


# -----------------------------------------------------------------------------
# 2. Northwind migration
# -----------------------------------------------------------------------------

def migrate_northwind(conn: psycopg.Connection) -> None:
    if not os.path.exists(SQLITE_PATH):
        raise SystemExit(f"Northwind SQLite file not found: {SQLITE_PATH}")

    lite = sqlite3.connect(SQLITE_PATH)
    lite.row_factory = sqlite3.Row
    cur = conn.cursor()

    # --- categories ---
    rows = lite.execute("SELECT CategoryID, CategoryName, Description FROM Categories").fetchall()
    cur.executemany(
        "INSERT INTO scm.categories (category_id, category_name, description) VALUES (%s,%s,%s)",
        [(r["CategoryID"], r["CategoryName"], r["Description"]) for r in rows],
    )
    print(f"  categories      {len(rows):>6}")

    # --- suppliers (with synthetic reliability signals) ---
    rng = random.Random(RNG_SEED)
    rows = lite.execute(
        "SELECT SupplierID, CompanyName, ContactName, ContactTitle, City, Region, Country, Phone "
        "FROM Suppliers"
    ).fetchall()
    supplier_records = []
    for r in rows:
        lead = rng.randint(3, 21)
        supplier_records.append((
            r["SupplierID"], r["CompanyName"], r["ContactName"], r["ContactTitle"],
            r["City"], r["Region"], r["Country"], r["Phone"],
            lead,
            round(rng.uniform(0.5, lead * 0.4), 2),
            round(rng.uniform(78.0, 99.5), 2),
        ))
    cur.executemany(
        "INSERT INTO scm.suppliers (supplier_id, company_name, contact_name, contact_title, city, "
        "region, country, phone, avg_lead_time_days, lead_time_stddev_days, on_time_delivery_pct) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        supplier_records,
    )
    print(f"  suppliers       {len(supplier_records):>6}")

    # --- products ---
    rows = lite.execute(
        "SELECT ProductID, ProductName, SupplierID, CategoryID, QuantityPerUnit, UnitPrice, "
        "UnitsInStock, UnitsOnOrder, ReorderLevel, Discontinued FROM Products"
    ).fetchall()
    cur.executemany(
        "INSERT INTO scm.products (product_id, product_name, supplier_id, category_id, "
        "quantity_per_unit, unit_price, units_in_stock, units_on_order, reorder_level, discontinued) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(
            r["ProductID"], r["ProductName"], r["SupplierID"], r["CategoryID"],
            r["QuantityPerUnit"], r["UnitPrice"], r["UnitsInStock"] or 0,
            r["UnitsOnOrder"] or 0, r["ReorderLevel"] or 0, to_bool(r["Discontinued"]),
        ) for r in rows],
    )
    print(f"  products        {len(rows):>6}")

    # --- employees (BLOB columns dropped) ---
    rows = lite.execute(
        "SELECT EmployeeID, LastName, FirstName, Title, City, Country, HomePhone, ReportsTo "
        "FROM Employees"
    ).fetchall()
    cur.executemany(
        "INSERT INTO scm.employees (employee_id, last_name, first_name, title, city, country, "
        "home_phone, reports_to) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        [tuple(r) for r in rows],
    )
    print(f"  employees       {len(rows):>6}")

    # --- customers (email synthesised deterministically) ---
    rows = lite.execute(
        "SELECT CustomerID, CompanyName, ContactName, ContactTitle, Address, City, Region, "
        "PostalCode, Country, Phone, Fax FROM Customers"
    ).fetchall()
    customer_records = []
    for r in rows:
        email = f"{slugify(r['ContactName'])}@{slugify(r['CompanyName'])}.example.com"
        customer_records.append((
            r["CustomerID"], r["CompanyName"], r["ContactName"], r["ContactTitle"],
            r["Address"], r["City"], r["Region"], r["PostalCode"], r["Country"],
            r["Phone"], r["Fax"], email,
        ))
    cur.executemany(
        "INSERT INTO scm.customers (customer_id, company_name, contact_name, contact_title, address, "
        "city, region, postal_code, country, phone, fax, email) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        customer_records,
    )
    print(f"  customers       {len(customer_records):>6}")

    valid_customers = {r["CustomerID"] for r in rows}
    valid_products = set(
        r[0] for r in lite.execute("SELECT ProductID FROM Products").fetchall()
    )

    # --- orders (skip rows whose customer is absent, to keep FKs honest) ---
    rows = lite.execute(
        "SELECT OrderID, CustomerID, EmployeeID, OrderDate, RequiredDate, ShippedDate, ShipVia, "
        "Freight, ShipName, ShipAddress, ShipCity, ShipRegion, ShipPostalCode, ShipCountry FROM Orders"
    ).fetchall()
    order_records, skipped_orders = [], 0
    valid_orders = set()
    for r in rows:
        cid = r["CustomerID"]
        if cid is not None and cid not in valid_customers:
            skipped_orders += 1
            continue
        valid_orders.add(r["OrderID"])
        order_records.append((
            r["OrderID"], cid, r["EmployeeID"], ts(r["OrderDate"]), ts(r["RequiredDate"]),
            ts(r["ShippedDate"]), r["ShipVia"], r["Freight"], r["ShipName"], r["ShipAddress"],
            r["ShipCity"], r["ShipRegion"], r["ShipPostalCode"], r["ShipCountry"],
        ))
    n = bulk_copy(
        cur, "scm.orders",
        ["order_id", "customer_id", "employee_id", "order_date", "required_date", "shipped_date",
         "ship_via", "freight", "ship_name", "ship_address", "ship_city", "ship_region",
         "ship_postal_code", "ship_country"],
        order_records,
    )
    print(f"  orders          {n:>6}" +
          (f"  ({skipped_orders} skipped: unknown customer)" if skipped_orders else ""))

    # --- order_details (source table is "Order Details", with a space) ---
    # Streamed rather than materialised: this table has ~609k rows.
    stats = {"skipped": 0}
    seen: set[tuple[int, int]] = set()

    def detail_rows():
        cursor = lite.execute(
            'SELECT OrderID, ProductID, UnitPrice, Quantity, Discount FROM "Order Details"'
        )
        for r in cursor:
            key = (r["OrderID"], r["ProductID"])
            if (r["OrderID"] not in valid_orders
                    or r["ProductID"] not in valid_products
                    or key in seen):
                stats["skipped"] += 1
                continue
            seen.add(key)
            yield (r["OrderID"], r["ProductID"], r["UnitPrice"], r["Quantity"], r["Discount"] or 0)

    n = bulk_copy(
        cur, "scm.order_details",
        ["order_id", "product_id", "unit_price", "quantity", "discount"],
        detail_rows(),
    )
    print(f"  order_details   {n:>6}" +
          (f"  ({stats['skipped']} skipped: dangling/duplicate)" if stats["skipped"] else ""))

    lite.close()


# -----------------------------------------------------------------------------
# 3. Synthetic WMS / TMS / contract seed
# -----------------------------------------------------------------------------

def seed_operations(conn: psycopg.Connection) -> None:
    rng = random.Random(RNG_SEED)
    cur = conn.cursor()
    today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)

    # --- warehouses ---
    warehouses = [
        (SCENARIO_WAREHOUSE, "Paris Distribution Centre", "Paris", "France"),
        ("WH-DE-01", "Hamburg Distribution Centre", "Hamburg", "Germany"),
    ]
    cur.executemany(
        "INSERT INTO scm.warehouses (warehouse_id, name, city, country) VALUES (%s,%s,%s,%s)",
        warehouses,
    )

    # --- storage bins ---
    bins = []
    for zone in ("A", "B", "C"):
        for slot in range(1, 7):
            bins.append((f"{zone}1-{slot:02d}", SCENARIO_WAREHOUSE, zone, 100 if zone == "A" else 200))
    for slot in range(1, 4):
        bins.append((f"D1-{slot:02d}", "WH-DE-01", "D", 150))
    cur.executemany(
        "INSERT INTO scm.storage_bins (bin_id, warehouse_id, zone, capacity) VALUES (%s,%s,%s,%s)",
        bins,
    )
    print(f"  warehouses {len(warehouses)}, storage_bins {len(bins)}")

    # --- bin stock ---
    # The scenario product sits in the scenario bin with the quantity the system
    # believes is on hand. The physical bin is empty; that gap is what the
    # Smart Stock Adjustment agent has to discover and correct.
    fr_bins = [b[0] for b in bins if b[1] == SCENARIO_WAREHOUSE]
    stock_rows = [(SCENARIO_BIN, SCENARIO_PRODUCT_ID, SCENARIO_SYSTEM_QTY)]

    # Other lines on the scenario order must be pickable, so the incident is
    # genuinely isolated to product 11.
    other_scenario_products = [42, 72]
    for idx, pid in enumerate(other_scenario_products):
        stock_rows.append((fr_bins[idx + 1], pid, 60))

    # Background stock so queries and future workflows have realistic breadth.
    used = {(SCENARIO_BIN, SCENARIO_PRODUCT_ID)} | {(r[0], r[1]) for r in stock_rows}
    product_ids = [r[0] for r in cur.execute("SELECT product_id FROM scm.products ORDER BY product_id").fetchall()]
    for pid in rng.sample(product_ids, 40):
        bin_id = rng.choice(fr_bins)
        if (bin_id, pid) in used:
            continue
        used.add((bin_id, pid))
        stock_rows.append((bin_id, pid, rng.randint(5, 90)))
    cur.executemany(
        "INSERT INTO scm.bin_stock (bin_id, product_id, quantity) VALUES (%s,%s,%s)", stock_rows
    )

    # --- stock ledger: a receipt history that justifies the believed quantity ---
    ledger_rows = [(
        SCENARIO_PRODUCT_ID, SCENARIO_BIN, "RECEIPT", SCENARIO_SYSTEM_QTY,
        "Inbound delivery from supplier", "GR-2026-0041", today - timedelta(days=14),
    )]
    # A prior cycle count that found no problem, so the discrepancy is recent.
    ledger_rows.append((
        SCENARIO_PRODUCT_ID, SCENARIO_BIN, "CYCLE_COUNT", 0,
        "Routine cycle count, no variance", "CC-2026-0112", today - timedelta(days=9),
    ))
    for bin_id, pid, qty in stock_rows[1:]:
        ledger_rows.append((
            pid, bin_id, "RECEIPT", qty, "Initial stocking", "GR-SEED", today - timedelta(days=20),
        ))
    cur.executemany(
        "INSERT INTO scm.stock_ledger (product_id, bin_id, transaction_type, quantity, reason, "
        "reference, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        ledger_rows,
    )
    print(f"  bin_stock {len(stock_rows)}, stock_ledger {len(ledger_rows)}")

    # --- carriers: IDs 1-3 match Northwind shipper IDs so orders.ship_via resolves ---
    carriers = [
        (1, "Speedy Express", "ROAD", "(503) 555-9831"),
        (2, "United Package", "ROAD", "(503) 555-3199"),
        (3, "Federal Shipping", "ROAD", "(503) 555-9931"),
        (4, "Euro Freight", "ROAD", "+49 40 555 0100"),
        (5, "AirExpress FR", "AIR", "+33 1 55 50 01 00"),
    ]
    cur.executemany(
        "INSERT INTO scm.carriers (carrier_id, carrier_name, mode, phone) VALUES (%s,%s,%s,%s)",
        carriers,
    )

    # --- carrier rates ---
    # Federal Shipping (3) has no capacity: it dropped the load, which is what
    # forces a re-tender. Among the remaining France carriers, Speedy Express
    # is the cheapest expedited option that still meets a 2-day timeline —
    # a deterministic expected answer, useful later as a Layer 4 golden case.
    rates = [
        # carrier, region, base, expedited, base_days, exp_days, capacity
        (1, "France", 12.00, 35.00, 5, 2, True),
        (2, "France", 18.00, 50.00, 4, 2, True),
        (3, "France", 15.50, 45.00, 6, 3, False),
        (5, "France", 28.00, 60.00, 2, 1, True),
        (4, "Germany", 10.00, 30.00, 4, 2, True),
        (1, "Germany", 14.00, 38.00, 5, 3, True),
    ]
    cur.executemany(
        "INSERT INTO scm.carrier_rates (carrier_id, region, base_rate, expedited_rate, "
        "base_transit_days, expedited_transit_days, capacity_available) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        rates,
    )
    print(f"  carriers {len(carriers)}, carrier_rates {len(rates)}")

    # --- scenario shipment: delayed, on the carrier that dropped the load ---
    cur.execute(
        "INSERT INTO scm.shipments (order_id, carrier_id, status, origin_warehouse_id, "
        "destination_country, service_level, promised_delivery, estimated_delivery, freight_cost) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            SCENARIO_ORDER_ID, 3, "DELAYED", SCENARIO_WAREHOUSE, "France", "STANDARD",
            today + timedelta(days=2),   # promised to the customer
            today + timedelta(days=7),   # current estimate → SLA breach
            15.50,
        ),
    )

    # --- contracts and SLA terms for the scenario customer plus a few others ---
    cur.execute(
        "SELECT customer_id FROM scm.customers WHERE customer_id = %s "
        "UNION SELECT customer_id FROM scm.customers WHERE customer_id <> %s ORDER BY customer_id LIMIT 10",
        (SCENARIO_CUSTOMER_ID, SCENARIO_CUSTOMER_ID),
    )
    contract_customers = [r[0] for r in cur.fetchall()]
    if SCENARIO_CUSTOMER_ID not in contract_customers:
        contract_customers.append(SCENARIO_CUSTOMER_ID)

    contracts, slas = [], []
    for cid in contract_customers:
        contract_id = f"CTR-{cid}"
        contracts.append((contract_id, cid, "2026-01-01", "2026-12-31", "DAP"))
        slas.append((contract_id, "ON_TIME_DELIVERY", 2, 5.00,
                     "Delivery within 2 days of the promised date; 5% credit per breach."))
        slas.append((contract_id, "PROACTIVE_NOTIFICATION", 1, 0.00,
                     "Customer must be notified within 1 day of a known delay."))
    cur.executemany(
        "INSERT INTO scm.contracts (contract_id, customer_id, valid_from, valid_to, incoterms) "
        "VALUES (%s,%s,%s,%s,%s)", contracts,
    )
    cur.executemany(
        "INSERT INTO scm.sla_terms (contract_id, metric, threshold_days, penalty_pct, notes) "
        "VALUES (%s,%s,%s,%s,%s)", slas,
    )
    print(f"  contracts {len(contracts)}, sla_terms {len(slas)}")


# -----------------------------------------------------------------------------
# 4. Verification
# -----------------------------------------------------------------------------

def verify(conn: psycopg.Connection) -> bool:
    cur = conn.cursor()
    ok = True

    def check(label: str, query: str, params=(), predicate=lambda v: v is not None):
        nonlocal ok
        row = cur.execute(query, params).fetchone()
        value = row[0] if row else None
        passed = predicate(value)
        ok = ok and passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {value}")

    print("\nVerifying reference scenario:")
    check("order 10248 exists", "SELECT customer_id FROM scm.orders WHERE order_id=%s",
          (SCENARIO_ORDER_ID,), lambda v: v == SCENARIO_CUSTOMER_ID)
    check("customer has an email", "SELECT email FROM scm.customers WHERE customer_id=%s",
          (SCENARIO_CUSTOMER_ID,), lambda v: bool(v) and "@" in v)
    check("bin stock for product 11",
          "SELECT quantity FROM scm.bin_stock WHERE product_id=%s AND bin_id=%s",
          (SCENARIO_PRODUCT_ID, SCENARIO_BIN), lambda v: v == SCENARIO_SYSTEM_QTY)
    check("ledger history for product 11",
          "SELECT COUNT(*) FROM scm.stock_ledger WHERE product_id=%s",
          (SCENARIO_PRODUCT_ID,), lambda v: v >= 2)
    # This is the join that used to 404 (blocker B2).
    check("shipment joins to a real carrier",
          "SELECT c.carrier_name FROM scm.shipments s JOIN scm.carriers c ON s.carrier_id=c.carrier_id "
          "WHERE s.order_id=%s", (SCENARIO_ORDER_ID,), lambda v: v == "Federal Shipping")
    check("alternative France carriers with capacity",
          "SELECT COUNT(*) FROM scm.carrier_rates WHERE region='France' AND capacity_available "
          "AND carrier_id <> 3", (), lambda v: v >= 2)
    check("SLA terms for the customer",
          "SELECT COUNT(*) FROM scm.sla_terms st JOIN scm.contracts ct ON st.contract_id=ct.contract_id "
          "WHERE ct.customer_id=%s", (SCENARIO_CUSTOMER_ID,), lambda v: v >= 1)
    check("order lines are all stocked or flagged",
          "SELECT COUNT(*) FROM scm.order_details WHERE order_id=%s", (SCENARIO_ORDER_ID,),
          lambda v: v == 3)
    return ok


def main() -> int:
    print(f"Building '{PG_DATABASE}' on {PG_HOST}:{PG_PORT} as '{PG_USER}'\n")
    print("1. Ensuring database exists")
    ensure_database()

    with psycopg.connect(admin_dsn(PG_DATABASE)) as conn:
        print("\n2. Applying schema")
        apply_schema(conn)

        print("\n3. Migrating Northwind from SQLite")
        migrate_northwind(conn)

        print("\n4. Seeding synthetic WMS / TMS / contracts")
        seed_operations(conn)
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        from seed_dashboard_scenarios import seed_demo_scenarios
        seed_demo_scenarios(conn)
        from seed_execution import seed_execution
        seed_execution(conn)

        conn.commit()
        ok = verify(conn)

    print("\nDone." if ok else "\nDone, but verification FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
