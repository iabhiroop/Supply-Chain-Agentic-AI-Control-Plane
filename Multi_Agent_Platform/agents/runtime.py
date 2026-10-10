"""Incident runtime: opens an incident, runs the workflow, and closes it out.

This is the seam between an inbound event and the agent graph. It owns the parts
of the lifecycle that must survive the LLM being wrong: idempotency, the audit
trail, per-agent run records, and the final resolution status (plan.md §4.1).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from langchain_core.messages import HumanMessage

from agents.config.db import get_conn
from agents.config.settings import settings
from agents.contracts import AgentResult, AgentStatus, IncidentEvent
from agents.graph.warehouse_graph import orchestrator_app
from agents.graph.warehouse_supervisor import required_agents


def open_incident(event: IncidentEvent) -> tuple[str, bool]:
    """Create an incident, or return the existing one for a redelivered event.

    Returns (incident_id, created). Idempotency is enforced by a unique key on
    the event's identity, so a duplicate event cannot trigger a second round of
    stock adjustments and carrier bookings (plan.md §4.1).
    """
    key = event.idempotency_key()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT incident_id FROM idempotency_keys WHERE event_key = %s", (key,)
        ).fetchone()
        if existing:
            return str(existing["incident_id"]), False

        incident_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO incidents (incident_id, tenant_id, event_type, status, severity, "
            "order_id, product_id, payload) VALUES (%s,%s,%s,'OPEN',%s,%s,%s,%s)",
            (incident_id, event.tenant_id, event.event_type, event.severity,
             event.order_id, event.product_id, json.dumps(event.model_dump(mode="json"))),
        )
        conn.execute(
            "INSERT INTO idempotency_keys (event_key, incident_id) VALUES (%s, %s)",
            (key, incident_id),
        )
        conn.execute(
            "INSERT INTO incident_events (incident_id, event, detail) VALUES (%s, 'OPENED', %s)",
            (incident_id, json.dumps(event.model_dump(mode="json"))),
        )
        return incident_id, True


def _record_agent_runs(incident_id: str, agent_results: dict[str, Any]) -> None:
    with get_conn() as conn:
        for name, payload in agent_results.items():
            try:
                result = AgentResult(**payload)
                status, confidence = result.status.value, result.confidence
            except Exception:  # noqa: BLE001
                status, confidence = "UNKNOWN", 0.0
            conn.execute(
                "INSERT INTO agent_runs (run_id, incident_id, agent_name, status, confidence, "
                "result, llm_calls, tool_calls, ended_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())",
                (str(uuid.uuid4()), incident_id, name, status, confidence,
                 json.dumps(payload, default=str),
                 payload.get("_llm_calls", 0), payload.get("_tool_calls", 0)),
            )


def close_incident(incident_id: str, status: str, summary: str, llm_calls: int = 0) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE incidents SET status = %s, resolution_summary = %s, llm_calls = %s, "
            "resolved_at = CASE WHEN %s IN ('RESOLVED','FAILED','CANCELLED') THEN now() END "
            "WHERE incident_id = %s",
            (status, summary, llm_calls, status, incident_id),
        )
        conn.execute(
            "INSERT INTO incident_events (incident_id, event, detail) VALUES (%s, %s, %s)",
            (incident_id, f"STATUS_{status}", json.dumps({"summary": summary})),
        )


def _derive_status(agent_results: dict[str, Any], event_type: str = "") -> tuple[str, str]:
    """Decide the incident outcome from the agents' structured results.

    Deliberately computed in code, not asked of a model: the incident's final
    status is an auditable business fact.
    """
    statuses: dict[str, AgentStatus] = {}
    for name, payload in agent_results.items():
        try:
            statuses[name] = AgentResult(**payload).status
        except Exception:  # noqa: BLE001
            continue

    if not statuses:
        return "FAILED", "No agent produced a usable result."

    if any(s is AgentStatus.NEEDS_HUMAN for s in statuses.values()):
        blocked = [n for n, s in statuses.items() if s is AgentStatus.NEEDS_HUMAN]
        return "AWAITING_APPROVAL", f"Awaiting a human decision from: {', '.join(blocked)}."

    if any(s is AgentStatus.FAILED for s in statuses.values()):
        failed = [n for n, s in statuses.items() if s is AgentStatus.FAILED]
        return "FAILED", f"Failed agents: {', '.join(failed)}."

    required = required_agents(event_type, agent_results)
    ok = {AgentStatus.SUCCESS, AgentStatus.NOT_APPLICABLE}
    if all(statuses.get(m) in ok for m in required):
        done = [n for n, s in statuses.items() if s is AgentStatus.SUCCESS]
        return "RESOLVED", f"Resolved end to end by: {', '.join(done)}."

    missing = [m for m in required if m not in statuses]
    return "IN_PROGRESS", (
        "Incomplete" + (f"; never ran: {', '.join(missing)}." if missing else ".")
    )


async def execute_workflow(
    incident_id: str,
    event: IncidentEvent,
    *,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the orchestrator for an already-opened incident."""
    close_incident(incident_id, "IN_PROGRESS", "Workflow started.")

    initial = {
        "messages": [HumanMessage(content=(
            f"{event.describe()}\n"
            "Coordinate the team to resolve this disruption end to end."
        ))],
        "incident_id": incident_id,
        "event": event.model_dump(mode="json"),
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
                "configurable": {"incident_id": incident_id, "thread_id": incident_id},
            },
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    elapsed = time.time() - started
    agent_results = final_state.get("agent_results", {}) or {}
    if agent_results:
        _record_agent_runs(incident_id, agent_results)

    if error:
        status, summary = "FAILED", f"Workflow raised {error}"
    else:
        status, summary = _derive_status(agent_results, event.event_type)

    close_incident(incident_id, status, summary, final_state.get("llm_calls", 0))

    return {
        "incident_id": incident_id,
        "created": True,
        "status": status,
        "summary": summary,
        "error": error,
        "elapsed_seconds": round(elapsed, 1),
        "steps": final_state.get("step_count", 0),
        "llm_calls": final_state.get("llm_calls", 0),
        "tool_calls": final_state.get("tool_calls", 0),
        "escalations": final_state.get("escalations", []),
        "agent_results": agent_results,
        "halt_reason": final_state.get("halt_reason", ""),
    }


async def run_incident(event: IncidentEvent, *, verbose: bool = True) -> dict[str, Any]:
    """Open an incident for an event and run the workflow to completion."""
    incident_id, created = open_incident(event)
    if not created:
        if verbose:
            print(f"Event already handled by incident {incident_id} (idempotent replay).")
        return {"incident_id": incident_id, "created": False, "status": "DUPLICATE"}

    return await execute_workflow(incident_id, event, verbose=verbose)
