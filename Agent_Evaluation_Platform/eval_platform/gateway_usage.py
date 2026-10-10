"""Join Layer 1 usage onto a Layer 4 trace by shared trace_id."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from eval_platform.config import settings


def gateway_dsn() -> str:
    return (
        f"host={settings.pg_host} port={settings.pg_port} user={settings.pg_user} "
        f"password={settings.pg_password} dbname={settings.gateway_pg_database}"
    )


def usage_for_trace(trace_id: str) -> dict[str, Any]:
    """Sum gateway usage_records whose extra_json.trace_id matches. Empty if unreachable."""
    empty = {"cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    if not trace_id:
        return empty
    try:
        with psycopg.connect(gateway_dsn(), row_factory=dict_row) as conn:
            row = conn.execute(
                """
                SELECT
                  COALESCE(SUM(cost_usd), 0) AS cost_usd,
                  COALESCE(SUM(input_tokens), 0) AS prompt_tokens,
                  COALESCE(SUM(output_tokens), 0) AS completion_tokens,
                  COUNT(*) AS calls
                FROM usage_records
                WHERE extra_json->>'trace_id' = %s
                """,
                (trace_id,),
            ).fetchone()
    except Exception:  # noqa: BLE001
        return empty
    return {
        "cost_usd": float((row or {}).get("cost_usd") or 0),
        "prompt_tokens": int((row or {}).get("prompt_tokens") or 0),
        "completion_tokens": int((row or {}).get("completion_tokens") or 0),
        "calls": int((row or {}).get("calls") or 0),
    }
