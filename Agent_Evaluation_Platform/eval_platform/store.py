"""Append-only trace and score storage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Json

from eval_platform.db import fetch_all, fetch_one, get_conn
from eval_platform.pii import redact


def _now() -> datetime:
    return datetime.now(timezone.utc)


def upsert_trace(payload: dict[str, Any]) -> str:
    trace_id = str(payload.get("trace_id") or uuid.uuid4())
    spans = payload.get("spans") or []
    started = payload.get("started_at")
    ended = payload.get("ended_at")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO traces (
                trace_id, tenant_id, workflow, transaction_id, status,
                started_at, ended_at, cost_usd, prompt_tokens, completion_tokens,
                source, metadata
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (trace_id) DO UPDATE SET
                status = EXCLUDED.status,
                ended_at = EXCLUDED.ended_at,
                cost_usd = traces.cost_usd + EXCLUDED.cost_usd,
                prompt_tokens = traces.prompt_tokens + EXCLUDED.prompt_tokens,
                completion_tokens = traces.completion_tokens + EXCLUDED.completion_tokens,
                metadata = traces.metadata || EXCLUDED.metadata
            """,
            (
                trace_id,
                payload.get("tenant_id") or "default",
                payload.get("workflow"),
                payload.get("transaction_id"),
                payload.get("status") or "OK",
                started,
                ended,
                float(payload.get("cost_usd") or 0),
                int(payload.get("prompt_tokens") or 0),
                int(payload.get("completion_tokens") or 0),
                payload.get("source") or "http",
                Json(redact(payload.get("metadata") or {})),
            ),
        )
        for span in spans:
            span_id = str(span.get("span_id") or uuid.uuid4())
            conn.execute(
                """
                INSERT INTO spans (
                    span_id, trace_id, parent_span_id, name, kind, status,
                    started_at, ended_at, duration_ms, model, prompt_id,
                    prompt_tokens, completion_tokens, cost_usd, cache_status,
                    input, output, error, attributes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (span_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    ended_at = COALESCE(EXCLUDED.ended_at, spans.ended_at),
                    duration_ms = COALESCE(EXCLUDED.duration_ms, spans.duration_ms),
                    model = COALESCE(EXCLUDED.model, spans.model),
                    prompt_tokens = spans.prompt_tokens + EXCLUDED.prompt_tokens,
                    completion_tokens = spans.completion_tokens + EXCLUDED.completion_tokens,
                    cost_usd = spans.cost_usd + EXCLUDED.cost_usd,
                    input = COALESCE(EXCLUDED.input, spans.input),
                    output = COALESCE(EXCLUDED.output, spans.output),
                    error = COALESCE(EXCLUDED.error, spans.error),
                    attributes = spans.attributes || EXCLUDED.attributes
                """,
                (
                    span_id,
                    trace_id,
                    span.get("parent_span_id"),
                    span.get("name") or "span",
                    span.get("kind") or "internal",
                    span.get("status") or "OK",
                    span.get("started_at"),
                    span.get("ended_at"),
                    span.get("duration_ms"),
                    span.get("model"),
                    span.get("prompt_id"),
                    int(span.get("prompt_tokens") or 0),
                    int(span.get("completion_tokens") or 0),
                    float(span.get("cost_usd") or 0),
                    span.get("cache_status"),
                    Json(redact(span.get("input"))) if span.get("input") is not None else None,
                    Json(redact(span.get("output"))) if span.get("output") is not None else None,
                    span.get("error"),
                    Json(redact(span.get("attributes") or {})),
                ),
            )
    return trace_id


def apply_usage(trace_id: str, usage: dict[str, Any]) -> None:
    """Overwrite trace cost/tokens with gateway totals for this trace_id."""
    if not trace_id:
        return
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE traces
               SET cost_usd = %s,
                   prompt_tokens = %s,
                   completion_tokens = %s,
                   metadata = metadata || %s
             WHERE trace_id = %s
            """,
            (
                float(usage.get("cost_usd") or 0),
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
                Json({"gateway_calls": int(usage.get("calls") or 0)}),
                trace_id,
            ),
        )


def insert_run(payload: dict[str, Any]) -> str:
    run_id = str(payload.get("run_id") or uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, trace_id, tenant_id, workflow, transaction_id,
                agent_version, status, summary, snapshot
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                summary = EXCLUDED.summary,
                snapshot = EXCLUDED.snapshot
            """,
            (
                run_id,
                payload.get("trace_id"),
                payload.get("tenant_id") or "default",
                payload.get("workflow"),
                payload.get("transaction_id"),
                payload.get("agent_version"),
                payload.get("status"),
                payload.get("summary"),
                Json(redact(payload.get("snapshot") or {})),
            ),
        )
    return run_id


def append_score(row: dict[str, Any]) -> str:
    score_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO scores (
                score_id, run_id, trace_id, scorer, scorer_version,
                metric, value, passed, rationale
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                score_id,
                row.get("run_id"),
                row.get("trace_id"),
                row["scorer"],
                row.get("scorer_version") or "1",
                row["metric"],
                float(row["value"]),
                row.get("passed"),
                row.get("rationale"),
            ),
        )
    return score_id


def list_traces(limit: int = 50, workflow: str | None = None, status: str | None = None) -> list[dict]:
    clauses = ["TRUE"]
    params: list[Any] = []
    if workflow:
        clauses.append("workflow = %s")
        params.append(workflow)
    if status:
        clauses.append("status = %s")
        params.append(status)
    params.append(limit)
    return fetch_all(
        f"SELECT * FROM traces WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT %s",
        tuple(params),
    )


def get_trace(trace_id: str) -> dict[str, Any] | None:
    trace = fetch_one("SELECT * FROM traces WHERE trace_id = %s", (trace_id,))
    if not trace:
        return None
    spans = fetch_all("SELECT * FROM spans WHERE trace_id = %s ORDER BY started_at NULLS LAST", (trace_id,))
    scores = fetch_all("SELECT * FROM scores WHERE trace_id = %s ORDER BY created_at", (trace_id,))
    run = fetch_one("SELECT * FROM runs WHERE trace_id = %s ORDER BY created_at DESC LIMIT 1", (trace_id,))
    return {**trace, "spans": spans, "scores": scores, "run": run}


def list_runs(limit: int = 40) -> list[dict]:
    return fetch_all(
        """
        SELECT r.*, t.cost_usd, t.prompt_tokens, t.completion_tokens
          FROM runs r
          LEFT JOIN traces t ON t.trace_id = r.trace_id
         ORDER BY r.created_at DESC
         LIMIT %s
        """,
        (limit,),
    )
