"""Deterministic taiwan-disruption pack. Additive: never DELETE P2P rows."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Json

from twin.apply_schema import apply as apply_schema
from twin.db import scm_conn

_ROOT = Path(__file__).resolve().parent.parent
CORPUS = _ROOT / "corpus"

DATASET = "twin-disruption-v1"

# ABC list price; XYZ is exactly +8%.
ABC_PRICE = 25.00
XYZ_PRICE = 27.00  # 25 * 1.08
ABC_LEAD = 21
XYZ_LEAD = 7

PLANTS = [
    ("PL-IN-CHN", "Chennai Assembly", "India"),
    ("PL-IN-BLR", "Bangalore Assembly", "India"),
    ("PL-US-AUS", "Austin Assembly", "United States"),
    ("PL-DE-MUC", "Munich Assembly", "Germany"),
    ("PL-SG-JUR", "Jurong DC", "Singapore"),
]

# Two plants under 10 days of cover; demand horizon 18 days > cover.
INVENTORY = [
    # plant_id, on_hand, safety_stock, daily_demand → days_of_cover
    ("PL-IN-CHN", 500, 200, 100),   # 5.00
    ("PL-IN-BLR", 800, 200, 100),   # 8.00
    ("PL-US-AUS", 2500, 400, 100),  # 25.00
    ("PL-DE-MUC", 3000, 400, 100),  # 30.00
    ("PL-SG-JUR", 1800, 300, 100),  # 18.00
]

DEMAND = [
    # plant_id, qty, horizon_days  (100/day × 18 = 1800)
    ("PL-IN-CHN", 1800, 18),
    ("PL-IN-BLR", 1800, 18),
    ("PL-US-AUS", 1800, 18),
    ("PL-DE-MUC", 1800, 18),
    ("PL-SG-JUR", 1800, 18),
]

OPEN_POS = [
    # po_id, vendor, plant, qty, reallocatable_qty
    ("PO-1842", "VEND-ABC", "PL-IN-CHN", 400, 160),
    ("PO-1843", "VEND-ABC", "PL-IN-BLR", 300, 0),
    ("PO-1844", "VEND-ABC", "PL-US-AUS", 200, 0),
    ("PO-1845", "VEND-ABC", "PL-DE-MUC", 150, 0),
    ("PO-1846", "VEND-ABC", "PL-SG-JUR", 250, 0),
    ("PO-1850", "VEND-ABC", "PL-IN-CHN", 100, 0),
    ("PO-1851", "VEND-ABC", "PL-IN-BLR", 80, 0),
    ("PO-1870", "VEND-ABC", "PL-US-AUS", 120, 0),
    ("PO-1880", "VEND-ABC", "PL-DE-MUC", 90, 0),
    ("PO-1860", "VEND-XYZ", "PL-SG-JUR", 50, 0),
]


def _upsert_plant(cur, plant_id: str, name: str, country: str) -> None:
    cur.execute(
        """
        INSERT INTO plants (plant_id, name, country)
        VALUES (%s, %s, %s)
        ON CONFLICT (plant_id) DO UPDATE SET name = EXCLUDED.name, country = EXCLUDED.country
        """,
        (plant_id, name, country),
    )


def seed_disruption(conn: psycopg.Connection) -> dict:
    """Load twin-disruption-v1. Does not DELETE execution_transactions / PR-1001."""
    apply_schema(conn)
    cur = conn.cursor()
    need_by = date.today() + timedelta(days=21)
    cur.execute(
        """
        INSERT INTO gl_accounts (gl_account, name) VALUES ('4000', 'Inventory receipts')
        ON CONFLICT (gl_account) DO NOTHING
        """
    )

    for plant_id, name, country in PLANTS:
        _upsert_plant(cur, plant_id, name, country)

    cur.execute(
        """
        INSERT INTO materials (material_id, description, uom, last_price, gl_account)
        VALUES ('MAT-SEMI-1', 'Power-management IC (SEMI-X)', 'EA', %s, '4000')
        ON CONFLICT (material_id) DO UPDATE
          SET description = EXCLUDED.description, last_price = EXCLUDED.last_price
        """,
        (ABC_PRICE,),
    )

    vendors = [
        (
            "VEND-ABC",
            "Taiwan Semiconductor Components",
            "ops@tsc-abc.example",
            "Taiwan",
            ABC_LEAD,
        ),
        (
            "VEND-XYZ",
            "Penang Alternate Silicon",
            "sales@pas-xyz.example",
            "Malaysia",
            XYZ_LEAD,
        ),
    ]
    for vid, name, email, country, lead in vendors:
        cur.execute(
            """
            INSERT INTO vendors
                (vendor_id, name, email, country, incoterms, payment_terms, moq, lead_time_days, active)
            VALUES (%s, %s, %s, %s, 'FOB', 'NET30', 1, %s, TRUE)
            ON CONFLICT (vendor_id) DO UPDATE SET
                name = EXCLUDED.name,
                email = EXCLUDED.email,
                country = EXCLUDED.country,
                lead_time_days = EXCLUDED.lead_time_days,
                active = TRUE
            """,
            (vid, name, email, country, lead),
        )

    vm_rows = [
        ("VEND-ABC", "MAT-SEMI-1", ABC_LEAD, ABC_PRICE, False),
        ("VEND-XYZ", "MAT-SEMI-1", XYZ_LEAD, XYZ_PRICE, True),
    ]
    for vendor_id, material_id, lead, price, alt in vm_rows:
        cur.execute(
            """
            INSERT INTO vendor_materials
                (vendor_id, material_id, lead_time_days, unit_price, is_alternate, eligible)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            ON CONFLICT (vendor_id, material_id) DO UPDATE SET
                lead_time_days = EXCLUDED.lead_time_days,
                unit_price = EXCLUDED.unit_price,
                is_alternate = EXCLUDED.is_alternate,
                eligible = TRUE
            """,
            (vendor_id, material_id, lead, price, alt),
        )

    for plant_id, on_hand, safety, daily in INVENTORY:
        cur.execute(
            """
            INSERT INTO inventory_positions
                (plant_id, material_id, on_hand, safety_stock, daily_demand, updated_at)
            VALUES (%s, 'MAT-SEMI-1', %s, %s, %s, now())
            ON CONFLICT (plant_id, material_id) DO UPDATE SET
                on_hand = EXCLUDED.on_hand,
                safety_stock = EXCLUDED.safety_stock,
                daily_demand = EXCLUDED.daily_demand,
                updated_at = now()
            """,
            (plant_id, on_hand, safety, daily),
        )

    for plant_id, qty, horizon in DEMAND:
        cur.execute(
            """
            INSERT INTO independent_demand (plant_id, material_id, qty, horizon_days, need_by)
            VALUES (%s, 'MAT-SEMI-1', %s, %s, %s)
            ON CONFLICT (plant_id, material_id) DO UPDATE SET
                qty = EXCLUDED.qty,
                horizon_days = EXCLUDED.horizon_days,
                need_by = EXCLUDED.need_by
            """,
            (plant_id, qty, horizon, need_by),
        )

    unit_by_vendor = {"VEND-ABC": ABC_PRICE, "VEND-XYZ": XYZ_PRICE}
    for po_id, vendor_id, plant_id, qty, realloc in OPEN_POS:
        cur.execute(
            """
            INSERT INTO twin_purchase_orders (
                po_id, vendor_id, material_id, plant_id, quantity, unit_price,
                promise_date, status, reallocatable_qty
            )
            VALUES (%s, %s, 'MAT-SEMI-1', %s, %s, %s, %s, 'OPEN', %s)
            ON CONFLICT (po_id) DO UPDATE SET
                vendor_id = EXCLUDED.vendor_id,
                material_id = EXCLUDED.material_id,
                plant_id = EXCLUDED.plant_id,
                quantity = EXCLUDED.quantity,
                unit_price = EXCLUDED.unit_price,
                promise_date = EXCLUDED.promise_date,
                status = 'OPEN',
                reallocatable_qty = EXCLUDED.reallocatable_qty
            """,
            (po_id, vendor_id, plant_id, qty, unit_by_vendor[vendor_id], need_by, realloc),
        )

    news_path = CORPUS / "news_abc_shutdown.json"
    news = json.loads(news_path.read_text(encoding="utf-8")) if news_path.is_file() else {
        "signal_id": "NEWS-ABC-SHUTDOWN",
        "headline": "Taiwan Semiconductor Components announces 14-day production shutdown",
        "payload": {
            "vendor_id": "VEND-ABC",
            "country": "Taiwan",
            "event": "production_shutdown",
            "duration_days": 14,
        },
    }
    cur.execute(
        """
        INSERT INTO twin_signals (signal_id, signal_type, entity_id, headline, payload, event_time)
        VALUES (%s, 'news', %s, %s, %s, now())
        ON CONFLICT (signal_id) DO UPDATE SET
            headline = EXCLUDED.headline,
            payload = EXCLUDED.payload,
            entity_id = EXCLUDED.entity_id,
            event_time = now()
        """,
        (
            news.get("signal_id", "NEWS-ABC-SHUTDOWN"),
            news.get("payload", {}).get("vendor_id", "VEND-ABC"),
            news.get("headline"),
            Json(news.get("payload") or {}),
        ),
    )

    return {
        "dataset": DATASET,
        "plants": len(PLANTS),
        "inventory": len(INVENTORY),
        "open_pos": len(OPEN_POS),
    }


def load(dataset: str = "disruption") -> dict:
    if dataset not in ("disruption", "twin-disruption-v1"):
        raise ValueError(f"unknown dataset {dataset!r}; Wave 1 supports 'disruption'")
    with scm_conn() as conn:
        counts = seed_disruption(conn)
    return counts
