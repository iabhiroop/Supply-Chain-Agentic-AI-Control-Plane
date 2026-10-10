"""Database access for the internal ERP/WMS/TMS API.

This is the relational system of record described in plan.md §6.2: it owns all
quantities, statuses, money and audit records. Mutating endpoints run inside a
single transaction and record an inverse payload so the orchestrator can
compensate a failed workflow.
"""

from __future__ import annotations

import json
import uuid

import psycopg

from agents.config.db import fetch_all, fetch_one, get_conn

__all__ = ["get_conn", "fetch_all", "fetch_one", "record_action"]


def record_action(
    conn: psycopg.Connection,
    *,
    agent_name: str,
    action_type: str,
    payload: dict,
    inverse: dict,
    incident_id: str | None = None,
    transaction_id: str | None = None,
) -> str:
    """Persist a mutating action together with the operation that undoes it.

    Runs inside the caller's transaction, so the action log and the mutation it
    describes commit atomically. An unknown incident_id is stored as NULL
    rather than rejected: losing the audit record would be worse than losing
    the link.
    """
    action_id = str(uuid.uuid4())
    linked = None
    if incident_id:
        exists = conn.execute(
            "SELECT 1 FROM incidents WHERE incident_id = %s", (incident_id,)
        ).fetchone()
        linked = incident_id if exists else None

    conn.execute(
        "INSERT INTO actions (action_id, incident_id, transaction_id, agent_name, "
        "action_type, payload, inverse) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            action_id,
            linked,
            transaction_id,
            agent_name,
            action_type,
            json.dumps(payload, default=str),
            json.dumps(inverse, default=str),
        ),
    )
    return action_id
