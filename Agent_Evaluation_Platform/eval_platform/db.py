"""Postgres access for schema eval only."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from eval_platform.config import settings


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        settings.pg_dsn(),
        row_factory=dict_row,
        options="-c search_path=eval,public",
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(query: str, params: tuple = ()) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return list(conn.execute(query, params).fetchall())


def fetch_one(query: str, params: tuple = ()) -> dict[str, Any] | None:
    with get_conn() as conn:
        return conn.execute(query, params).fetchone()
