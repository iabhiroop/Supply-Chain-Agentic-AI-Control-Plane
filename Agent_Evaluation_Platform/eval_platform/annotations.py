"""Human review queue and operator-edit import."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Json

from eval_platform.db import fetch_all, get_conn


def queue(limit: int = 20) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT r.run_id, r.transaction_id, r.status, r.summary,
               (SELECT COUNT(*) FROM scores s WHERE s.run_id = r.run_id AND s.passed IS FALSE) AS fails
          FROM runs r
         ORDER BY fails DESC, r.created_at DESC
         LIMIT %s
        """,
        (limit,),
    )


def add_annotation(run_id: str, annotator: str, labels: dict, rubric: dict | None = None) -> str:
    annotation_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO annotations (annotation_id, run_id, annotator, rubric, labels)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (annotation_id, run_id, annotator, Json(rubric or {}), Json(labels)),
        )
    return annotation_id


def add_feedback(run_id: str, source: str, rating: int | None, comment: str | None) -> str:
    feedback_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO feedback (feedback_id, run_id, source, rating, comment)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (feedback_id, run_id, source, rating, comment),
        )
    return feedback_id
