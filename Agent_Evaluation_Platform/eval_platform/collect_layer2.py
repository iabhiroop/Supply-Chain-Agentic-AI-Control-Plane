"""Pull Layer 2 ERP snapshots into eval traces without writing ERP."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row

from eval_platform.config import settings
from eval_platform.gateway_usage import usage_for_trace
from eval_platform.scorers import disruption as disruption_rules
from eval_platform.scorers import judge, rules
from eval_platform.store import append_score, apply_usage, insert_run, upsert_trace


def _scm_conn():
    return psycopg.connect(
        settings.pg_dsn(),
        row_factory=dict_row,
        options="-c search_path=scm,public",
    )


def _jsonable(value: Any) -> Any:
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def load_layer2_snapshot(transaction_id: str) -> dict[str, Any]:
    """Read-only copy of the P2P job. Does not import Layer 2 Python packages."""
    with _scm_conn() as conn:
        tx = conn.execute(
            "SELECT * FROM execution_transactions WHERE transaction_id = %s",
            (transaction_id,),
        ).fetchone()
        if not tx:
            raise KeyError(transaction_id)
        pr = conn.execute(
            "SELECT * FROM purchase_requisitions WHERE transaction_id = %s LIMIT 1",
            (transaction_id,),
        ).fetchone()
        material = None
        policy = conn.execute("SELECT * FROM execution_policy WHERE policy_id = 'DEFAULT'").fetchone()
        if pr:
            material = conn.execute(
                "SELECT * FROM materials WHERE material_id = %s", (pr["material_id"],)
            ).fetchone()
        quotes = conn.execute(
            "SELECT * FROM quotes WHERE transaction_id = %s", (transaction_id,)
        ).fetchall()
        po = conn.execute(
            "SELECT * FROM exec_purchase_orders WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1",
            (transaction_id,),
        ).fetchone()
        booking = conn.execute(
            "SELECT * FROM bookings WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1",
            (transaction_id,),
        ).fetchone()
        gr = conn.execute(
            "SELECT * FROM goods_receipts_exec WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1",
            (transaction_id,),
        ).fetchone()
        match = conn.execute(
            "SELECT * FROM invoice_matches WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1",
            (transaction_id,),
        ).fetchone()
        actions = conn.execute(
            "SELECT * FROM actions WHERE transaction_id = %s ORDER BY created_at",
            (transaction_id,),
        ).fetchall()
        runs = conn.execute(
            "SELECT * FROM agent_runs WHERE transaction_id = %s ORDER BY started_at",
            (transaction_id,),
        ).fetchall()
        agent_results = {}
        for run in runs:
            raw = run.get("result")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = {"summary": raw}
            if run.get("agent_name"):
                agent_results[run["agent_name"]] = raw or {}
        snap = {
            "transaction": dict(tx),
            "pr": dict(pr) if pr else None,
            "material": dict(material) if material else None,
            "policy": dict(policy) if policy else {},
            "quotes": [dict(q) for q in quotes],
            "po": dict(po) if po else None,
            "booking": dict(booking) if booking else None,
            "gr": dict(gr) if gr else None,
            "match": dict(match) if match else None,
            "actions": [dict(a) for a in actions],
            "agent_runs": [dict(r) for r in runs],
            "agent_results": agent_results,
            "steps": sum(int(r.get("tool_calls") or 0) for r in runs),
        }
        return _jsonable(snap)


def persist_and_score(
    snap: dict[str, Any],
    *,
    expected: dict | None = None,
    judge_llm: bool = False,
    trace_id: str,
    run_id: str,
    transaction_id: str | None = None,
    workflow: str = "p2p_execution",
    source: str = "layer2_pull",
) -> dict[str, Any]:
    tx = snap.get("transaction") or {}
    rec = snap.get("recommendation") if isinstance(snap.get("recommendation"), dict) else {}
    status = tx.get("status") or snap.get("status") or "OK"
    parent = f"wf-{transaction_id}" if transaction_id else f"wf-{run_id}"
    spans = [{
        "span_id": parent,
        "parent_span_id": None,
        "name": "workflow.disruption" if workflow == "disruption" else "workflow.execute_transaction",
        "kind": "workflow",
        "status": status,
        "attributes": {
            "transaction_id": transaction_id,
            "pr_id": (snap.get("pr") or {}).get("pr_id"),
            "affected_po_ids": rec.get("affected_po_ids"),
        },
        "output": rec or None,
    }]
    for run in snap.get("agent_runs") or []:
        spans.append({
            "span_id": str(run.get("run_id") or uuid.uuid4()),
            "parent_span_id": parent,
            "name": f"agent.{run.get('agent_name')}",
            "kind": "agent",
            "status": run.get("status") or "OK",
            "output": run.get("result") if isinstance(run.get("result"), dict) else {"raw": run.get("result")},
            "attributes": {
                "agent_name": run.get("agent_name"),
                "tool_calls": run.get("tool_calls"),
                "llm_calls": run.get("llm_calls"),
                "confidence": run.get("confidence"),
            },
        })
    for name, payload in (snap.get("agent_results") or {}).items():
        if any(s.get("name") == f"agent.{name}" for s in spans):
            continue
        spans.append({
            "span_id": f"agent-{run_id}-{name}",
            "parent_span_id": parent,
            "name": f"agent.{name}",
            "kind": "agent",
            "status": (payload or {}).get("status") if isinstance(payload, dict) else "OK",
            "output": payload if isinstance(payload, dict) else {"raw": payload},
        })
    for action in snap.get("actions") or []:
        spans.append({
            "span_id": str(action.get("action_id") or uuid.uuid4()),
            "parent_span_id": parent,
            "name": f"tool.{action.get('action_type')}",
            "kind": "tool",
            "status": action.get("status") or "OK",
            "input": action.get("payload"),
            "output": action.get("inverse"),
            "attributes": {"agent_name": action.get("agent_name")},
        })
    upsert_trace({
        "trace_id": trace_id,
        "tenant_id": settings.tenant_id,
        "workflow": workflow,
        "transaction_id": transaction_id,
        "status": status,
        "source": source,
        "spans": spans,
        "metadata": {
            "pr_id": (snap.get("pr") or {}).get("pr_id"),
            "stage": tx.get("stage"),
            "vendor_id": rec.get("vendor_id"),
        },
    })
    usage = usage_for_trace(trace_id)
    if usage.get("calls"):
        apply_usage(trace_id, usage)
    summary = rec.get("summary") or (f"{tx.get('stage')} / {tx.get('status')}" if tx else status)
    stored_run = insert_run({
        "run_id": run_id,
        "trace_id": trace_id,
        "tenant_id": settings.tenant_id,
        "workflow": workflow,
        "transaction_id": transaction_id,
        "status": status,
        "summary": summary,
        "snapshot": snap,
    })
    scores = _score_snapshot(snap, expected)
    if judge_llm:
        scores.extend(judge.score_run(snap, expected))
    for row in scores:
        row["run_id"] = stored_run
        row["trace_id"] = trace_id
        append_score(row)
    return {
        "trace_id": trace_id,
        "run_id": stored_run,
        "scores": scores,
        "snapshot": snap,
        "gateway_usage": usage,
    }


def _is_disruption(snap: dict[str, Any], expected: dict | None) -> bool:
    expected = expected or {}
    if expected.get("workflow") == "disruption":
        return True
    if snap.get("workflow") == "disruption":
        return True
    return snap.get("recommendation") is not None


def _score_snapshot(snap: dict[str, Any], expected: dict | None) -> list[dict[str, Any]]:
    if _is_disruption(snap, expected):
        return disruption_rules.score_run(snap, expected)
    return rules.score_run(snap, expected)


def load_disruption_snapshot() -> dict[str, Any]:
    """Latest RECOMMEND_DISRUPTION action from Layer 2. Read-only."""
    with _scm_conn() as conn:
        row = conn.execute(
            """
            SELECT action_id, agent_name, payload, inverse, created_at
              FROM actions
             WHERE action_type = 'RECOMMEND_DISRUPTION'
             ORDER BY created_at DESC
             LIMIT 1
            """
        ).fetchone()
    if not row:
        raise KeyError("RECOMMEND_DISRUPTION")
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    return _jsonable({
        "workflow": "disruption",
        "status": "RECOMMENDED",
        "action_id": row.get("action_id"),
        "agent_name": row.get("agent_name"),
        "recommendation": payload,
        "payload": payload,
        "inverse": row.get("inverse"),
        "created_at": row.get("created_at"),
        "agent_results": {},
    })


def ingest_latest_disruption(*, expected: dict | None = None, judge_llm: bool = False, item_id: str = "golden-abc-shutdown") -> dict[str, Any]:
    try:
        snap = load_disruption_snapshot()
        source = "layer2_pull"
        trace_id = f"l2-disruption-eval-{item_id}"
    except KeyError:
        fixture = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data",
            "fixtures",
            "disruption_abc.json",
        )
        snap = json.load(open(fixture, encoding="utf-8"))
        source = "fixture"
        trace_id = f"fixture-{item_id}"
    return persist_and_score(
        snap,
        expected=expected,
        judge_llm=judge_llm,
        trace_id=trace_id,
        run_id=f"run-{item_id}",
        transaction_id=None,
        workflow="disruption",
        source=source,
    )


def ingest_transaction(transaction_id: str, *, expected: dict | None = None, judge_llm: bool = False) -> dict[str, Any]:
    snap = load_layer2_snapshot(transaction_id)
    tx = snap["transaction"]
    trace_id = (tx.get("context") or {}).get("trace_id") or f"l2-{transaction_id}"
    return persist_and_score(
        snap,
        expected=expected,
        judge_llm=judge_llm,
        trace_id=trace_id,
        run_id=f"run-{transaction_id}",
        transaction_id=transaction_id,
        source="layer2_pull",
    )


def ingest_snapshot(
    snapshot: dict[str, Any],
    *,
    item_id: str,
    expected: dict | None = None,
    judge_llm: bool = False,
) -> dict[str, Any]:
    workflow = "disruption" if _is_disruption(snapshot, expected) else "p2p_fixture"
    return persist_and_score(
        snapshot,
        expected=expected,
        judge_llm=judge_llm,
        trace_id=f"fixture-{item_id}",
        run_id=f"run-{item_id}",
        transaction_id=(snapshot.get("transaction") or {}).get("transaction_id"),
        workflow=workflow,
        source="fixture",
    )
