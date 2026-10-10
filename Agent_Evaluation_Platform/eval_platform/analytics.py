"""Quality / cost / latency rollups and simple drift alerts."""

from __future__ import annotations

from eval_platform.db import fetch_all, fetch_one, get_conn
from psycopg.types.json import Json
import uuid


def quality() -> dict:
    totals = fetch_one(
        """
        SELECT
          COUNT(*) FILTER (WHERE passed IS TRUE) AS passed,
          COUNT(*) FILTER (WHERE passed IS FALSE) AS failed,
          COUNT(*) AS n
        FROM scores
        WHERE metric = 'hard_blockers'
        """
    ) or {}
    by_metric = fetch_all(
        """
        SELECT metric, scorer, AVG(value) AS avg_value,
               COUNT(*) FILTER (WHERE passed IS TRUE)::float / NULLIF(COUNT(*),0) AS pass_rate
          FROM scores
         GROUP BY metric, scorer
         ORDER BY metric
        """
    )
    traces = fetch_one(
        """
        SELECT COALESCE(AVG(cost_usd),0) AS avg_cost,
               COALESCE(AVG(EXTRACT(EPOCH FROM (ended_at - started_at))),0) AS avg_latency_s,
               COUNT(*) AS traces
          FROM traces
        """
    ) or {}
    return {"hard_blockers": totals, "metrics": by_metric, "traces": traces}


def maybe_alert() -> list[dict]:
    row = fetch_one(
        """
        SELECT COUNT(*) FILTER (WHERE passed IS FALSE)::float
               / NULLIF(COUNT(*),0) AS fail_rate
          FROM scores WHERE metric = 'hard_blockers'
        """
    )
    alerts = []
    fail_rate = float((row or {}).get("fail_rate") or 0)
    if fail_rate > 0.2:
        recent = fetch_one(
            """
            SELECT alert_id FROM alerts
             WHERE kind = 'quality_drop' AND created_at > now() - interval '1 hour'
             LIMIT 1
            """
        )
        msg = f"Hard-blocker fail rate {fail_rate:.0%} exceeds 20%."
        if not recent:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO alerts (alert_id, kind, message, payload) VALUES (%s,%s,%s,%s)",
                    (str(uuid.uuid4()), "quality_drop", msg, Json({"fail_rate": fail_rate})),
                )
        alerts.append({"kind": "quality_drop", "message": msg})
    return alerts
