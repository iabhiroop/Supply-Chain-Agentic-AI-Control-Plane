"""Persist chat sessions and messages in PostgreSQL."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Json

from agents.config.db import fetch_all, fetch_one, get_conn


def get_session(session_id: str) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM chat_sessions WHERE session_id = %s", (session_id,))


def create_session(
    *,
    tenant_id: str,
    user_id: str,
    channel: str,
    incident_id: str | None = None,
) -> dict[str, Any]:
    session_id = str(uuid.uuid4())
    with get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO chat_sessions (session_id, tenant_id, user_id, channel, incident_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (session_id, tenant_id, user_id, channel, incident_id),
        ).fetchone()
    return dict(row)


def touch_session(
    session_id: str,
    *,
    incident_id: str | None = None,
    pending_proposal: dict[str, Any] | None | object = ...,
) -> None:
    sets = ["updated_at = now()"]
    params: list[Any] = []
    if incident_id is not None:
        sets.append("incident_id = %s")
        params.append(incident_id)
    if pending_proposal is not ...:
        sets.append("pending_proposal = %s")
        params.append(Json(pending_proposal) if pending_proposal is not None else None)
    params.append(session_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE chat_sessions SET {', '.join(sets)} WHERE session_id = %s",
            tuple(params),
        )


def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    intent: str | None = None,
    cards: list[dict[str, Any]] | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, intent, content, cards)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, role, intent, content, Json(cards) if cards else None),
        )


def list_messages(session_id: str, limit: int = 40) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT message_id, role, intent, content, cards, created_at
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY created_at ASC, message_id ASC
        LIMIT %s
        """,
        (session_id, limit),
    )
