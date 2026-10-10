"""Durable agent memory: prompts, turns, blackboard, experience, A2A messages."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from psycopg.types.json import Json

from agents.config.db import fetch_all, fetch_one, get_conn
from agents.execution.trace import current_trace_id
from agents.memory.prompts import seed_rows, workflow_for

_SQL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "database",
    "agent_memory.sql",
)
_ready = False


def ensure() -> None:
    """Apply DDL + seed charters. Best-effort; never raises to callers of pack_turn."""
    global _ready
    if _ready:
        return
    try:
        with open(_SQL_PATH, encoding="utf-8") as fh:
            ddl = fh.read()
        with get_conn() as conn:
            conn.execute(ddl)
            for row in seed_rows():
                conn.execute(
                    """
                    INSERT INTO agent_prompt_versions (
                        prompt_id, agent_name, kind, version, body, active, workflow
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (prompt_id) DO UPDATE SET
                        body = EXCLUDED.body,
                        active = EXCLUDED.active,
                        workflow = EXCLUDED.workflow
                    """,
                    (
                        row["prompt_id"],
                        row["agent_name"],
                        row["kind"],
                        row["version"],
                        row["body"],
                        row["active"],
                        row.get("workflow") or "p2p",
                    ),
                )
        _ready = True
    except Exception:  # noqa: BLE001
        _ready = False


def active_prompt(
    agent_name: str,
    kind: str = "charter",
    *,
    workflow: str | None = None,
) -> dict[str, Any] | None:
    ensure()
    wf = workflow_for(agent_name, workflow=workflow)
    try:
        row = fetch_one(
            """
            SELECT prompt_id, agent_name, kind, version, body, workflow
              FROM agent_prompt_versions
             WHERE agent_name = %s AND kind = %s AND active AND workflow = %s
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (agent_name, kind, wf),
        )
        if row:
            return row
        return fetch_one(
            """
            SELECT prompt_id, agent_name, kind, version, body, workflow
              FROM agent_prompt_versions
             WHERE agent_name = %s AND kind = %s AND active
             ORDER BY created_at DESC
             LIMIT 1
            """,
            (agent_name, kind),
        )
    except Exception:  # noqa: BLE001
        return None


def active_body(agent_name: str, kind: str = "system", *, workflow: str | None = None) -> str | None:
    row = active_prompt(agent_name, kind, workflow=workflow)
    return (row or {}).get("body") if row else None


def record_turn(
    *,
    agent_name: str,
    result: Any,
    tool_calls: int = 0,
    workflow: str | None = None,
    facts: dict[str, Any] | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> None:
    ensure()
    trace_id = trace_id or current_trace_id() or "unknown"
    wf = workflow_for(agent_name, workflow=workflow)
    status = getattr(getattr(result, "status", None), "value", None) or (
        result.get("status") if isinstance(result, dict) else None
    )
    summary = getattr(result, "summary", None) if not isinstance(result, dict) else result.get("summary")
    findings = getattr(result, "findings", None) if not isinstance(result, dict) else result.get("findings")
    evidence = getattr(result, "evidence", None) if not isinstance(result, dict) else result.get("evidence")
    prompt = active_prompt(agent_name, "charter", workflow=wf) or active_prompt(
        agent_name, "system", workflow=wf
    )
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_turns (
                    turn_id, trace_id, run_id, workflow, agent_name, prompt_id,
                    status, summary, findings, evidence, tool_calls
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    trace_id,
                    run_id or trace_id,
                    wf,
                    agent_name,
                    (prompt or {}).get("prompt_id"),
                    status,
                    summary or "",
                    Json(findings or []),
                    Json(evidence or []),
                    int(tool_calls or 0),
                ),
            )
            for key, value in (facts or {}).items():
                conn.execute(
                    """
                    INSERT INTO blackboard_facts (trace_id, fact_key, value, source_agent, evidence)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (trace_id, fact_key) DO UPDATE SET
                        value = EXCLUDED.value,
                        source_agent = EXCLUDED.source_agent,
                        evidence = EXCLUDED.evidence,
                        updated_at = now()
                    """,
                    (
                        trace_id,
                        key,
                        Json(value),
                        agent_name,
                        (summary or "")[:400],
                    ),
                )
    except Exception:  # noqa: BLE001
        return


def put_fact(key: str, value: Any, *, source_agent: str, evidence: str | None = None, trace_id: str | None = None) -> None:
    ensure()
    trace_id = trace_id or current_trace_id() or "unknown"
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO blackboard_facts (trace_id, fact_key, value, source_agent, evidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (trace_id, fact_key) DO UPDATE SET
                    value = EXCLUDED.value,
                    source_agent = EXCLUDED.source_agent,
                    evidence = EXCLUDED.evidence,
                    updated_at = now()
                """,
                (trace_id, key, Json(value), source_agent, evidence),
            )
    except Exception:  # noqa: BLE001
        return


def get_facts(trace_id: str | None = None) -> dict[str, Any]:
    ensure()
    trace_id = trace_id or current_trace_id()
    if not trace_id:
        return {}
    try:
        rows = fetch_all(
            "SELECT fact_key, value, source_agent, evidence FROM blackboard_facts WHERE trace_id = %s",
            (trace_id,),
        )
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for row in rows:
        out[row["fact_key"]] = {
            "value": row["value"],
            "source_agent": row["source_agent"],
            "evidence": row["evidence"],
        }
    return out


def peer_citations(state: dict[str, Any], *names: str) -> list[str]:
    results = state.get("agent_results") or {}
    out = []
    for name in names:
        payload = results.get(name) or {}
        if not isinstance(payload, dict):
            continue
        summary = payload.get("summary")
        if summary:
            out.append(f"peer.{name}: {str(summary)[:240]}")
    return out


def record_message(*, from_agent: str, kind: str, payload: Any, trace_id: str | None = None) -> None:
    ensure()
    trace_id = trace_id or current_trace_id() or "unknown"
    body = payload
    if not isinstance(body, (dict, list)):
        body = {"raw": payload}
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (message_id, trace_id, from_agent, kind, payload)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), trace_id, from_agent, kind, Json(body)),
            )
    except Exception:  # noqa: BLE001
        return


def record_experience(
    *,
    flow: str,
    summary: str,
    outcome: dict[str, Any] | None = None,
    vendor_id: str | None = None,
    material_id: str | None = None,
    po_id: str | None = None,
    pr_id: str | None = None,
    trace_id: str | None = None,
    prompt_ids: list[str] | None = None,
) -> None:
    ensure()
    trace_id = trace_id or current_trace_id()
    ids = prompt_ids or []
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO experience_summaries (
                    experience_id, flow, vendor_id, material_id, po_id, pr_id,
                    trace_id, summary, outcome, prompt_ids
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    flow,
                    vendor_id,
                    material_id,
                    po_id,
                    pr_id,
                    trace_id,
                    summary or flow,
                    Json(outcome or {}),
                    ids,
                ),
            )
    except Exception:  # noqa: BLE001
        return


def last_experience(
    *,
    flow: str | None = None,
    vendor_id: str | None = None,
    po_id: str | None = None,
) -> dict[str, Any] | None:
    ensure()
    clauses = ["TRUE"]
    params: list[Any] = []
    if flow:
        clauses.append("flow = %s")
        params.append(flow)
    if vendor_id:
        clauses.append("vendor_id = %s")
        params.append(vendor_id)
    if po_id:
        clauses.append("po_id = %s")
        params.append(po_id)
    try:
        return fetch_one(
            f"""
            SELECT * FROM experience_summaries
             WHERE {' AND '.join(clauses)}
             ORDER BY created_at DESC
             LIMIT 1
            """,
            tuple(params),
        )
    except Exception:  # noqa: BLE001
        return None


def already_applied(po_id: str | None) -> dict[str, Any] | None:
    if not po_id:
        return None
    return last_experience(flow="disruption_apply", po_id=po_id)


def run_memory(trace_id: str) -> dict[str, Any]:
    ensure()
    empty = {
        "trace_id": trace_id,
        "turns": [],
        "blackboard": [],
        "messages": [],
        "experiences": [],
        "prompts": [],
    }
    try:
        return _run_memory_loaded(trace_id)
    except Exception:  # noqa: BLE001
        return empty


def _run_memory_loaded(trace_id: str) -> dict[str, Any]:
    turns = fetch_all(
        """
        SELECT t.*, p.version AS prompt_version, p.kind AS prompt_kind, p.body AS prompt_body
          FROM agent_turns t
          LEFT JOIN agent_prompt_versions p ON p.prompt_id = t.prompt_id
         WHERE t.trace_id = %s
         ORDER BY t.created_at
        """,
        (trace_id,),
    )
    facts = fetch_all(
        "SELECT * FROM blackboard_facts WHERE trace_id = %s ORDER BY fact_key",
        (trace_id,),
    )
    messages = fetch_all(
        "SELECT * FROM agent_messages WHERE trace_id = %s ORDER BY created_at",
        (trace_id,),
    )
    experiences = fetch_all(
        "SELECT * FROM experience_summaries WHERE trace_id = %s ORDER BY created_at",
        (trace_id,),
    )
    prompt_ids = sorted({t.get("prompt_id") for t in turns if t.get("prompt_id")})
    prompts = []
    if prompt_ids:
        prompts = fetch_all(
            "SELECT prompt_id, agent_name, kind, version, body, active FROM agent_prompt_versions WHERE prompt_id = ANY(%s)",
            (prompt_ids,),
        )
    return {
        "trace_id": trace_id,
        "turns": _jsonable(turns),
        "blackboard": _jsonable(facts),
        "messages": _jsonable(messages),
        "experiences": _jsonable(experiences),
        "prompts": _jsonable(prompts),
    }


def _jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        item = {}
        for k, v in dict(row).items():
            if hasattr(v, "isoformat"):
                item[k] = v.isoformat()
            elif isinstance(v, uuid.UUID):
                item[k] = str(v)
            else:
                item[k] = v
        out.append(item)
    return out
