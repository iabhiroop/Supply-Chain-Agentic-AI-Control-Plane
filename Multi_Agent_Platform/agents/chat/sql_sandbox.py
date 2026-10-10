"""Validate and execute SELECT-only SQL against chat_ro views."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from psycopg import sql as psql

from agents.chat.schema_card import SCHEMA_NAME, WarehouseRole, qualified_allowlist
from agents.config.db import get_conn
from agents.config.settings import settings

ALLOWED_FUNCTIONS = frozenset({
    "now", "coalesce", "greatest", "least", "extract", "date_part",
    "lower", "upper", "length", "round", "abs",
    "count", "sum", "avg", "min", "max",
    "current_date", "current_timestamp", "current_time",
    "cast", "nullif", "trim", "concat", "substring",
    "to_char", "date_trunc",
})

BLOCKED_FUNCTION_SUBSTRINGS = ("pg_", "dblink", "lo_", "set_config", "pg_sleep")


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _table_ref(table: exp.Table) -> str:
    name = (table.name or "").lower()
    schema = (table.db or "").lower()
    if schema:
        return f"{schema}.{name}"
    return name


def validate_sql(sql: str, role: WarehouseRole | str) -> tuple[str | None, str | None]:
    """Return (rewritten_sql, error). rewritten_sql is None on failure."""
    text = (sql or "").strip().rstrip(";")
    if not text:
        return None, "Empty SQL."
    if ";" in text:
        return None, "Multiple statements are not allowed."
    lowered = text.lower()
    if "--" in text or "/*" in text:
        return None, "Comments are not allowed in copilot SQL."
    for banned in (" insert ", " update ", " delete ", " drop ", " alter ",
                   " create ", " grant ", " revoke ", " truncate ", " copy ",
                   " into ", " for update"):
        if banned in f" {lowered} ":
            return None, f"Disallowed keyword:{banned.strip()}."

    try:
        parsed = sqlglot.parse(text, read="postgres")
    except ParseError as exc:
        return None, f"Could not parse SQL: {exc}"

    if not parsed or parsed[0] is None:
        return None, "Could not parse SQL."
    if len(parsed) != 1:
        return None, "Multiple statements are not allowed."

    stmt = parsed[0]
    if isinstance(stmt, exp.Command):
        return None, f"Disallowed statement: {stmt.name}."
    if stmt.find(exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Merge):
        return None, "Only SELECT queries are allowed."
    if not stmt.find(exp.Select):
        return None, "Only SELECT queries are allowed."

    allow = {n.lower() for n in qualified_allowlist(role)}
    for table in stmt.find_all(exp.Table):
        ref = _table_ref(table)
        bare = (table.name or "").lower()
        schema = (table.db or "").lower()
        if schema in {"scm", "pg_catalog", "information_schema", "public"}:
            return None, f"Relation {ref} is not in the warehouse read-only catalog."
        if schema and schema != SCHEMA_NAME:
            return None, f"Relation {ref} is not in the warehouse read-only catalog."
        if bare not in allow and ref not in allow:
            return None, f"View {ref or bare} is not allowed for this role."
        if not schema:
            table.set("db", SCHEMA_NAME)

    for anon in stmt.find_all(exp.Anonymous):
        fname = (anon.name or str(anon.this) or "").lower()
        if fname and (
            fname not in ALLOWED_FUNCTIONS
            or any(part in fname for part in BLOCKED_FUNCTION_SUBSTRINGS)
        ):
            return None, f"Function {fname} is not allowed."

    # Cap LIMIT
    limit_node = stmt.find(exp.Limit)
    cap = settings.chat_sql_row_limit
    if limit_node is None:
        stmt = stmt.limit(cap)
    else:
        try:
            n = int(limit_node.expression.this if hasattr(limit_node.expression, "this") else limit_node.expression)
            if n > cap:
                stmt = stmt.limit(cap)
        except (TypeError, ValueError):
            stmt = stmt.limit(cap)

    rewritten = stmt.sql(dialect="postgres")
    return rewritten, None


def run_readonly(sql: str, role: WarehouseRole | str) -> dict[str, Any]:
    rewritten, error = validate_sql(sql, role)
    if error:
        return {"ok": False, "sql": sql, "rows": [], "error": error, "row_count": 0}

    try:
        with get_conn() as conn:
            conn.execute(f"SET statement_timeout = {int(settings.chat_sql_timeout_ms)}")
            try:
                conn.execute(psql.SQL("SET ROLE {}").format(psql.Identifier(settings.chat_pg_role)))
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "sql": rewritten,
                    "rows": [],
                    "error": f"Could not assume read-only role: {exc}",
                    "row_count": 0,
                }
            conn.execute(f"SET search_path TO {SCHEMA_NAME}")
            cur = conn.execute(rewritten)
            raw = cur.fetchmany(settings.chat_sql_row_limit)
            rows = [{k: _jsonable(v) for k, v in dict(r).items()} for r in raw]
        return {"ok": True, "sql": rewritten, "rows": rows, "error": None, "row_count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "sql": rewritten,
            "rows": [],
            "error": f"{type(exc).__name__}: {exc}",
            "row_count": 0,
        }
