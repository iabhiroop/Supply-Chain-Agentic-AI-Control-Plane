"""Apply eval schema."""

from __future__ import annotations

import os

import psycopg

from eval_platform.config import settings

HERE = os.path.dirname(os.path.abspath(__file__))


def apply() -> None:
    sql = open(os.path.join(HERE, "schema.sql"), encoding="utf-8").read()
    with psycopg.connect(settings.pg_dsn()) as conn:
        conn.execute(sql)
        conn.commit()
    print("eval schema applied.")


if __name__ == "__main__":
    apply()
