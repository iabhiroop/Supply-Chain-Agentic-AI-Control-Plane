"""Run an execution transaction through the P2P stage machine."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage

from agents.config.db import get_conn
from agents.config.settings import settings
from agents.contracts import AgentResult, AgentStatus
from agents.execution import store
from agents.graph.graph import orchestrator_app


def _record_agent_runs(transaction_id: str, agent_results: dict[str, Any]) -> None:
    with get_conn() as conn:
        for name, payload in agent_results.items():
            try:
                result = AgentResult(**payload)
                status, confidence = result.status.value, result.confidence
            except Exception:  # noqa: BLE001
                status, confidence = "UNKNOWN", 0.0
            conn.execute(
                """
                INSERT INTO agent_runs (
                    run_id, incident_id, transaction_id, agent_name, status, confidence,
                    result, llm_calls, tool_calls, ended_at, prompt_id, prompt_version
                ) VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    transaction_id,
                    name,
                    status,
                    confidence,
                    json.dumps(payload, default=str),
                    payload.get("_llm_calls", 0),
                    payload.get("_tool_calls", 0),
                    payload.get("_prompt_id"),
                    payload.get("_prompt_version"),
                ),
            )


def _derive_status(snap: dict, agent_results: dict[str, Any]) -> tuple[str, str]:
    tx = snap.get("transaction") or {}
    stage, status = tx.get("stage"), tx.get("status")
    if status == "SETTLED" or stage == "SETTLED":
        return "SETTLED", "Purchase-to-pay settled."
    if status == "CANCELLED" or stage == "CANCELLED":
        return "CANCELLED", "Transaction cancelled."
    if status == "AWAITING_HUMAN" or stage in {"AWAITING_HUMAN", "PO_PENDING_APPROVAL"}:
        return "AWAITING_HUMAN", tx.get("halt_reason") or "Human approval required."
    statuses: dict[str, AgentStatus] = {}
    for name, payload in agent_results.items():
        try:
            statuses[name] = AgentResult(**payload).status
        except Exception:  # noqa: BLE001
            continue
    if any(s is AgentStatus.NEEDS_HUMAN for s in statuses.values()):
        return "AWAITING_HUMAN", "A specialist requested a human decision."
    if any(s is AgentStatus.FAILED for s in statuses.values()):
        failed = [n for n, s in statuses.items() if s is AgentStatus.FAILED]
        return "FAILED", f"Failed agents: {', '.join(failed)}."
    if any(s is AgentStatus.PARTIAL for s in statuses.values()):
        return "OPEN", "Waiting on a partner acknowledgement."
    return status or "OPEN", f"Stage {stage}."


async def execute_transaction(
    transaction_id: str,
    *,
    verbose: bool = True,
    trace_id: str | None = None,
) -> dict[str, Any]:
    from agents.execution.trace import mint_trace_id, reset_trace_id, set_trace_id
    from agents.memory.store import ensure

    ensure()
    snap = store.get_snapshot(transaction_id)
    tx = snap["transaction"]
    ctx = tx.get("context") or {}
    trace_id = trace_id or ctx.get("trace_id") or mint_trace_id(transaction_id)
    token = set_trace_id(trace_id)
    with get_conn() as conn:
        store.merge_context(conn, transaction_id, {"trace_id": trace_id})

    initial = {
        "messages": [HumanMessage(content=(
            f"Execute transaction {transaction_id} for PR {tx.get('pr_id')}. "
            f"Current stage {tx.get('stage')}."
        ))],
        "transaction_id": transaction_id,
        "stage": tx.get("stage"),
        "tx_context": {**ctx, "trace_id": trace_id},
        "incident_id": "",
        "event": {},
        "agent_results": {},
        "completed_agents": [],
        "step_count": 0,
        "llm_calls": 0,
        "tool_calls": 0,
        "escalations": [],
    }

    started = time.time()
    final_state: dict[str, Any] = {}
    error: str | None = None
    try:
        final_state = await orchestrator_app.ainvoke(
            initial,
            config={
                "recursion_limit": settings.workflow_max_steps * 2,
                "configurable": {
                    "transaction_id": transaction_id,
                    "trace_id": trace_id,
                    "thread_id": trace_id,
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    elapsed = time.time() - started
    agent_results = final_state.get("agent_results", {}) or {}
    if agent_results:
        _record_agent_runs(transaction_id, agent_results)

    snap = store.get_snapshot(transaction_id)
    if error:
        status, summary = "FAILED", f"Workflow raised {error}"
    else:
        status, summary = _derive_status(snap, agent_results)
        if status != "AWAITING_HUMAN":
            try:
                from agents.execution.hitl import park_from_agent_results

                parked = park_from_agent_results(transaction_id, agent_results)
                if parked and not parked.get("error"):
                    snap = store.get_snapshot(transaction_id)
                    status, summary = "AWAITING_HUMAN", parked.get("question") or "Human decision required."
            except Exception:  # noqa: BLE001
                pass

    result = {
        "transaction_id": transaction_id,
        "trace_id": trace_id,
        "status": status,
        "stage": (snap["transaction"] or {}).get("stage"),
        "summary": summary,
        "error": error,
        "elapsed_seconds": round(elapsed, 1),
        "steps": final_state.get("step_count", 0),
        "llm_calls": final_state.get("llm_calls", 0),
        "tool_calls": final_state.get("tool_calls", 0),
        "escalations": final_state.get("escalations", []),
        "agent_results": agent_results,
        "halt_reason": final_state.get("halt_reason") or (snap["transaction"] or {}).get("halt_reason") or "",
        "snapshot": snap,
    }
    try:
        from agents.execution.eval_emit import emit_transaction_trace

        emit_transaction_trace(result)
    except Exception:  # noqa: BLE001
        pass
    try:
        from agents.execution.graph_memory import emit_from_workflow

        emit_from_workflow(result)
    except Exception:  # noqa: BLE001
        pass
    reset_trace_id(token)
    return result
