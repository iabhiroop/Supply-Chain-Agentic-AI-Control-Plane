from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from graphrag.config import settings


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        settings.pg_dsn(),
        row_factory=dict_row,
        options="-c search_path=graphrag,public",
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def scm_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        settings.pg_dsn(),
        row_factory=dict_row,
        options="-c search_path=scm,public",
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return list(conn.execute(sql, params).fetchall())


def fetch_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()
