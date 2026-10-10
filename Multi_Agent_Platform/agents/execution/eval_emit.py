"""Fire-and-forget trace emit to Layer 4. Must never break a workflow."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

EVAL_URL = os.getenv("SCM_EVAL_URL", "http://127.0.0.1:8002")


def emit_transaction_trace(result: dict[str, Any]) -> None:
    if os.getenv("SCM_EVAL_EMIT", "true").lower() != "true":
        return
    tx_id = result.get("transaction_id")
    payload = {
        "trace_id": result.get("trace_id") or f"l2-{tx_id}",
        "tenant_id": os.getenv("SCM_TENANT_ID", "default"),
        "workflow": "p2p_execution",
        "transaction_id": tx_id,
        "status": result.get("status"),
        "source": "layer2_push",
        "cost_usd": float(result.get("cost_usd") or 0),
        "prompt_tokens": int(result.get("prompt_tokens") or 0),
        "completion_tokens": int(result.get("completion_tokens") or 0),
        "metadata": {"stage": result.get("stage"), "error": result.get("error")},
        "spans": [
            {
                "span_id": f"wf-{tx_id}",
                "name": "workflow.execute_transaction",
                "kind": "workflow",
                "status": result.get("status") or "OK",
                "duration_ms": (result.get("elapsed_seconds") or 0) * 1000,
                "attributes": {
                    "steps": result.get("steps"),
                    "llm_calls": result.get("llm_calls"),
                    "tool_calls": result.get("tool_calls"),
                },
            }
        ],
        "run": {
            "run_id": f"run-{tx_id}",
            "transaction_id": tx_id,
            "status": result.get("status"),
            "summary": result.get("summary"),
            "workflow": "p2p_execution",
        },
    }
    for name, agent in (result.get("agent_results") or {}).items():
        payload["spans"].append({
            "span_id": f"agent-{tx_id}-{name}",
            "parent_span_id": f"wf-{tx_id}",
            "name": f"agent.{name}",
            "kind": "agent",
            "status": (agent or {}).get("status") or "OK",
            "output": agent if isinstance(agent, dict) else {"raw": agent},
        })
    data = json.dumps(payload, default=str).encode()
    req = urllib.request.Request(
        EVAL_URL.rstrip("/") + "/v1/traces",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=2)
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def emit_disruption_trace(result: dict[str, Any]) -> None:
    """Fire-and-forget disruption rec to Layer 4. Must never break the workflow."""
    if os.getenv("SCM_EVAL_EMIT", "true").lower() != "true":
        return
    rec = result.get("recommendation") or {}
    run_id = result.get("run_id") or "disruption"
    trace_id = result.get("trace_id") or f"l2-disruption-{run_id}"
    payload = {
        "trace_id": trace_id,
        "tenant_id": os.getenv("SCM_TENANT_ID", "default"),
        "workflow": "disruption",
        "status": result.get("status") or "RECOMMENDED",
        "source": "layer2_push",
        "metadata": {
            "vendor_id": result.get("vendor_id"),
            "material_id": result.get("material_id"),
            "action_id": result.get("action_id"),
        },
        "spans": [
            {
                "span_id": f"wf-{run_id}",
                "name": "workflow.disruption",
                "kind": "workflow",
                "status": result.get("status") or "OK",
                "output": rec if isinstance(rec, dict) else {"raw": rec},
                "attributes": {
                    "completed_agents": result.get("completed_agents"),
                    "tool_calls": result.get("tool_calls"),
                },
            }
        ],
        "run": {
            "run_id": f"run-{run_id}",
            "status": result.get("status"),
            "summary": rec.get("summary") if isinstance(rec, dict) else "",
            "workflow": "disruption",
        },
    }
    for name, agent in (result.get("agent_results") or {}).items():
        payload["spans"].append({
            "span_id": f"agent-{run_id}-{name}",
            "parent_span_id": f"wf-{run_id}",
            "name": f"agent.{name}",
            "kind": "agent",
            "status": (agent or {}).get("status") or "OK",
            "output": agent if isinstance(agent, dict) else {"raw": agent},
        })
    data = json.dumps(payload, default=str).encode()
    req = urllib.request.Request(
        EVAL_URL.rstrip("/") + "/v1/traces",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=2)
    except (urllib.error.URLError, TimeoutError, OSError):
        return
