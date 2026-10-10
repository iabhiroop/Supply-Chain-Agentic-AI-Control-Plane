from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from twin.config import settings


@contextmanager
def scm_conn(*, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        settings.pg_dsn(),
        row_factory=dict_row,
        options="-c search_path=scm,public",
        autocommit=autocommit,
        connect_timeout=8,
    )
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with scm_conn() as conn:
        return list(conn.execute(sql, params).fetchall())


def fetch_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with scm_conn() as conn:
        return conn.execute(sql, params).fetchone()
