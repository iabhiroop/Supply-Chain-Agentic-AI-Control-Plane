"""Postgres access for execution transactions, ERP documents, and memory."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Json

from agents.config.db import get_conn
from agents.contracts import TransactionStage


def jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def _rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [jsonable(dict(r)) for r in conn.execute(sql, params).fetchall()]


def _row(conn: psycopg.Connection, sql: str, params: tuple = ()) -> dict | None:
    found = conn.execute(sql, params).fetchone()
    return jsonable(dict(found)) if found else None


def append_event(conn: psycopg.Connection, transaction_id: str, event: str, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO transaction_events (transaction_id, event, detail) VALUES (%s, %s, %s)",
        (transaction_id, event, Json(detail or {})),
    )


def merge_context(conn: psycopg.Connection, transaction_id: str, patch: dict) -> None:
    conn.execute(
        """
        UPDATE execution_transactions
           SET context = context || %s::jsonb, updated_at = now()
         WHERE transaction_id = %s
        """,
        (json.dumps(patch, default=str), transaction_id),
    )


def set_stage(
    conn: psycopg.Connection,
    transaction_id: str,
    stage: str,
    *,
    status: str | None = None,
    halt_reason: str | None = None,
    context_patch: dict | None = None,
    event: str | None = None,
) -> None:
    if context_patch:
        merge_context(conn, transaction_id, context_patch)
    conn.execute(
        """
        UPDATE execution_transactions
           SET stage = %s,
               status = COALESCE(%s, status),
               halt_reason = %s,
               updated_at = now()
         WHERE transaction_id = %s
        """,
        (stage, status, halt_reason, transaction_id),
    )
    append_event(conn, transaction_id, event or f"STAGE_{stage}", {
        "stage": stage,
        "status": status,
        "halt_reason": halt_reason,
    })


def load_transaction(conn: psycopg.Connection, transaction_id: str) -> dict | None:
    return _row(conn, "SELECT * FROM execution_transactions WHERE transaction_id = %s", (transaction_id,))


def snapshot(conn: psycopg.Connection, transaction_id: str) -> dict:
    tx = load_transaction(conn, transaction_id)
    if tx is None:
        raise KeyError(f"Unknown transaction {transaction_id}")

    pr = _row(
        conn,
        "SELECT * FROM purchase_requisitions WHERE transaction_id = %s ORDER BY created_at LIMIT 1",
        (transaction_id,),
    )

    material = None
    cost_center = None
    if pr:
        material = _row(conn, "SELECT * FROM materials WHERE material_id = %s", (pr["material_id"],))
        cost_center = _row(conn, "SELECT * FROM cost_centers WHERE cost_center = %s", (pr["cost_center"],))

    return {
        "transaction": tx,
        "pr": pr,
        "material": material,
        "cost_center": cost_center,
        "policy": _row(conn, "SELECT * FROM execution_policy WHERE policy_id = 'DEFAULT'") or {
            "policy_id": "DEFAULT",
            "price_deviation_pct": 5,
            "date_slip_days": 5,
            "approval_amount_usd": 5000,
        },
        "approval_rules": _rows(conn, "SELECT * FROM approval_rules"),
        "vendors": _rows(conn, "SELECT * FROM vendors WHERE active = TRUE ORDER BY vendor_id"),
        "rfqs": _rows(conn, "SELECT * FROM rfqs WHERE transaction_id = %s ORDER BY sent_at", (transaction_id,)),
        "quotes": _rows(conn, "SELECT * FROM quotes WHERE transaction_id = %s ORDER BY created_at", (transaction_id,)),
        "po": _row(conn, "SELECT * FROM exec_purchase_orders WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1", (transaction_id,)),
        "booking": _row(conn, "SELECT * FROM bookings WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1", (transaction_id,)),
        "tracking": _rows(conn, "SELECT * FROM tracking_events WHERE transaction_id = %s ORDER BY created_at", (transaction_id,)),
        "gr": _row(conn, "SELECT * FROM goods_receipts_exec WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1", (transaction_id,)),
        "supplier_invoice": _row(conn, "SELECT * FROM supplier_invoices WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1", (transaction_id,)),
        "freight_invoice": _row(conn, "SELECT * FROM freight_invoices WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1", (transaction_id,)),
        "match": _row(conn, "SELECT * FROM invoice_matches WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1", (transaction_id,)),
        "unread": _rows(
            conn,
            "SELECT * FROM channel_messages WHERE transaction_id = %s AND read_at IS NULL AND direction = 'IN' ORDER BY created_at",
            (transaction_id,),
        ),
        "messages": _rows(
            conn,
            "SELECT * FROM channel_messages WHERE transaction_id = %s ORDER BY created_at",
            (transaction_id,),
        ),
        "events": _rows(
            conn,
            "SELECT * FROM transaction_events WHERE transaction_id = %s ORDER BY created_at",
            (transaction_id,),
        ),
        "contracted_rate": _row(
            conn,
            "SELECT * FROM contracted_rates WHERE plant_id = %s ORDER BY rate_usd LIMIT 1",
            ((pr or {}).get("plant_id") or "WH-FR-01",),
        ),
    }


def get_snapshot(transaction_id: str) -> dict:
    with get_conn() as conn:
        return snapshot(conn, transaction_id)


def list_transactions(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        return _rows(
            conn,
            """
            SELECT t.transaction_id, t.pr_id, t.stage, t.status, t.halt_reason,
                   t.created_at, t.updated_at, pr.material_id, pr.quantity, pr.plant_id
            FROM execution_transactions t
            LEFT JOIN purchase_requisitions pr ON pr.transaction_id = t.transaction_id
            ORDER BY t.updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )


def list_memory() -> dict[str, list[dict]]:
    with get_conn() as conn:
        return {
            "supplier": _rows(conn, "SELECT * FROM supplier_memory ORDER BY created_at DESC LIMIT 50"),
            "lane": _rows(conn, "SELECT * FROM lane_memory ORDER BY created_at DESC LIMIT 50"),
            "commercial": _rows(conn, "SELECT * FROM commercial_memory ORDER BY created_at DESC LIMIT 50"),
            "exception": _rows(conn, "SELECT * FROM exception_memory ORDER BY created_at DESC LIMIT 50"),
        }


def write_supplier_memory(conn: psycopg.Connection, vendor_id: str, transaction_id: str, hours: float, note: str) -> None:
    conn.execute(
        """
        INSERT INTO supplier_memory (vendor_id, transaction_id, response_hours, reliability_note)
        VALUES (%s, %s, %s, %s)
        """,
        (vendor_id, transaction_id, hours, note),
    )


def write_lane_memory(conn: psycopg.Connection, region: str, carrier_name: str, transit_days: int, transaction_id: str) -> None:
    conn.execute(
        """
        INSERT INTO lane_memory (region, carrier_name, transit_days, transaction_id)
        VALUES (%s, %s, %s, %s)
        """,
        (region, carrier_name, transit_days, transaction_id),
    )


def write_commercial_memory(
    conn: psycopg.Connection,
    vendor_id: str,
    material_id: str,
    quoted: float,
    paid: float | None,
    transaction_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO commercial_memory (vendor_id, material_id, quoted_price, paid_price, transaction_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (vendor_id, material_id, quoted, paid, transaction_id),
    )


def write_exception_memory(
    conn: psycopg.Connection,
    transaction_id: str,
    root_cause: str,
    recovery: str,
    *,
    approved: bool,
    effective: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO exception_memory (transaction_id, root_cause, recovery_action, approved, effective)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (transaction_id, root_cause, recovery, approved, effective),
        )


HITL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS hitl_requests (
    request_id         UUID PRIMARY KEY,
    transaction_id     UUID REFERENCES execution_transactions(transaction_id) ON DELETE SET NULL,
    workflow           TEXT NOT NULL DEFAULT 'p2p',
    kind               TEXT NOT NULL DEFAULT 'model',
    agent_name         TEXT,
    step               TEXT NOT NULL DEFAULT 'decide',
    question           TEXT NOT NULL,
    options            JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommended_option TEXT,
    rationale          TEXT,
    confidence         DOUBLE PRECISION,
    evidence           JSONB NOT NULL DEFAULT '[]'::jsonb,
    context            JSONB NOT NULL DEFAULT '{}'::jsonb,
    status             TEXT NOT NULL DEFAULT 'open',
    selected_option    TEXT,
    human_note         TEXT,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    answered_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hitl_open ON hitl_requests(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hitl_tx ON hitl_requests(transaction_id);
"""


def ensure_hitl_table(conn: psycopg.Connection) -> None:
    conn.execute(HITL_TABLE_SQL)


def insert_hitl_request(conn: psycopg.Connection, row: dict[str, Any]) -> dict:
    ensure_hitl_table(conn)
    conn.execute(
        """
        INSERT INTO hitl_requests (
            request_id, transaction_id, workflow, kind, agent_name, step, question,
            options, recommended_option, rationale, confidence, evidence, context, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row["request_id"],
            row.get("transaction_id"),
            row.get("workflow") or "p2p",
            row.get("kind") or "model",
            row.get("agent_name") or "",
            row.get("step") or "decide",
            row["question"],
            Json(row.get("options") or []),
            row.get("recommended_option"),
            row.get("rationale") or "",
            row.get("confidence"),
            Json(row.get("evidence") or []),
            Json(row.get("context") or {}),
            row.get("status") or "open",
        ),
    )
    return get_hitl_request(conn, row["request_id"]) or row


def get_hitl_request(conn: psycopg.Connection, request_id: str) -> dict | None:
    ensure_hitl_table(conn)
    return _row(conn, "SELECT * FROM hitl_requests WHERE request_id = %s", (request_id,))


def get_open_hitl_request(conn: psycopg.Connection, transaction_id: str | None = None, request_id: str | None = None) -> dict | None:
    ensure_hitl_table(conn)
    if request_id:
        found = get_hitl_request(conn, request_id)
        if found and found.get("status") == "open":
            return found
        return found
    if transaction_id:
        return _row(
            conn,
            """
            SELECT * FROM hitl_requests
             WHERE transaction_id = %s AND status = 'open'
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (transaction_id,),
        )
    return None


def list_open_hitl_requests(limit: int = 40) -> list[dict]:
    with get_conn() as conn:
        ensure_hitl_table(conn)
        return _rows(
            conn,
            """
            SELECT r.*, t.pr_id, t.stage, t.status AS tx_status, t.halt_reason
              FROM hitl_requests r
              LEFT JOIN execution_transactions t ON t.transaction_id = r.transaction_id
             WHERE r.status = 'open'
             ORDER BY r.created_at DESC
             LIMIT %s
            """,
            (limit,),
        )


def close_hitl_request(
    conn: psycopg.Connection,
    request_id: str,
    *,
    status: str,
    selected_option: str | None = None,
    human_note: str | None = None,
) -> None:
    ensure_hitl_table(conn)
    conn.execute(
        """
        UPDATE hitl_requests
           SET status = %s, selected_option = %s, human_note = %s, answered_at = now()
         WHERE request_id = %s
        """,
        (status, selected_option, human_note, request_id),
    )


TERMINAL_STAGES = {TransactionStage.SETTLED.value, TransactionStage.AWAITING_HUMAN.value}
