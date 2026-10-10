"""Apply GraphRAG Postgres schema."""

from __future__ import annotations

import os

import psycopg

from graphrag.config import settings

HERE = os.path.dirname(os.path.abspath(__file__))


def apply() -> None:
    sql = open(os.path.join(HERE, "schema.sql"), encoding="utf-8").read()
    with psycopg.connect(settings.pg_dsn()) as conn:
        conn.execute(sql)
        conn.commit()
    print("graphrag schema applied.")


if __name__ == "__main__":
    apply()
