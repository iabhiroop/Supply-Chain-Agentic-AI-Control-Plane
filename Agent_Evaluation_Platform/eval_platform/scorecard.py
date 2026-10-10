"""Reviewer-facing scorecard: correctness, groundedness, tools, latency, tokens, cost, success."""

from __future__ import annotations

from typing import Any

from eval_platform.db import fetch_all, fetch_one
from eval_platform.store import get_trace, list_runs

P2P_GOLDEN = "aaaaaaaa-bbbb-cccc-dddd-000000000001"


def summarize_scores(
    scores: list[dict[str, Any]] | None,
    *,
    trace: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scores = scores or []
    by = {s.get("metric"): s for s in scores if s.get("metric")}
    hard = by.get("hard_blockers")
    domain = [s for s in scores if s.get("metric") not in {"hard_blockers", "budget_steps"}]
    n = len(domain) or 1
    passed_n = sum(1 for s in domain if s.get("passed"))
    correctness = round(passed_n / n, 4)

    grounded = by.get("cover_pct_grounded") or by.get("safety_pii")
    tools = by.get("tool_trajectory") or by.get("po_1842") or by.get("reallocate")
    success = bool(hard.get("passed")) if hard else all(s.get("passed") for s in domain)

    trace = trace or {}
    usage = usage or {}
    tokens = int(trace.get("prompt_tokens") or 0) + int(trace.get("completion_tokens") or 0)
    if not tokens:
        tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        tokens = tokens or int(usage.get("total_tokens") or 0)
    cost = float(trace.get("cost_usd") or usage.get("cost_usd") or usage.get("total_cost_usd") or 0)
    latency_ms = None
    if trace.get("started_at") and trace.get("ended_at"):
        try:
            latency_ms = (trace["ended_at"] - trace["started_at"]).total_seconds() * 1000
        except Exception:  # noqa: BLE001
            latency_ms = None
    if latency_ms is None and usage.get("avg_latency_ms") is not None:
        latency_ms = usage.get("avg_latency_ms")

    return {
        "correctness": correctness,
        "groundedness": 1.0 if grounded and grounded.get("passed") else (0.0 if grounded else None),
        "tools": 1.0 if tools and tools.get("passed") else (0.0 if tools else None),
        "latency_ms": latency_ms,
        "tokens": tokens,
        "cost_usd": round(cost, 6),
        "success": success,
        "hard_blockers": hard,
        "metrics": scores,
    }


def _latest_run(workflow: str | None = None, transaction_id: str | None = None) -> dict[str, Any] | None:
    if transaction_id:
        row = fetch_one(
            """
            SELECT r.*, t.cost_usd, t.prompt_tokens, t.completion_tokens, t.started_at, t.ended_at
              FROM runs r
              LEFT JOIN traces t ON t.trace_id = r.trace_id
             WHERE r.transaction_id = %s
             ORDER BY r.created_at DESC
             LIMIT 1
            """,
            (transaction_id,),
        )
        return row
    if workflow:
        row = fetch_one(
            """
            SELECT r.*, t.cost_usd, t.prompt_tokens, t.completion_tokens, t.started_at, t.ended_at
              FROM runs r
              LEFT JOIN traces t ON t.trace_id = r.trace_id
             WHERE r.workflow = %s
             ORDER BY r.created_at DESC
             LIMIT 1
            """,
            (workflow,),
        )
        return row
    rows = list_runs(1)
    return rows[0] if rows else None


def _card_for_run(run: dict[str, Any] | None, label: str) -> dict[str, Any]:
    if not run:
        return {"label": label, "available": False}
    trace_id = run.get("trace_id")
    scores = fetch_all("SELECT * FROM scores WHERE trace_id = %s ORDER BY created_at", (trace_id,)) if trace_id else []
    trace = get_trace(trace_id) if trace_id else None
    summary = summarize_scores(scores, trace=trace or run)
    return {
        "label": label,
        "available": True,
        "trace_id": trace_id,
        "run_id": run.get("run_id"),
        "workflow": run.get("workflow"),
        "status": run.get("status"),
        "summary": run.get("summary"),
        **summary,
    }


def last_scorecard(trace_id: str | None = None) -> dict[str, Any]:
    if trace_id:
        trace = get_trace(trace_id)
        if not trace:
            return {"trace_id": trace_id, "available": False}
        return {
            "trace_id": trace_id,
            "available": True,
            **summarize_scores(trace.get("scores") or [], trace=trace),
            "run": trace.get("run"),
        }
    return {
        "taiwan": _card_for_run(_latest_run(workflow="disruption"), "last Taiwan / abc_shutdown"),
        "p2p": _card_for_run(_latest_run(transaction_id=P2P_GOLDEN), "last P2P golden PR-1001"),
    }
