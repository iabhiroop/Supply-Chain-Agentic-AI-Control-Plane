"""Apply System of Execution ERP tables without rebuilding logistics_scm."""

from __future__ import annotations

import os
import sys

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "erp_execution.sql")
MEMORY_PATH = os.path.join(HERE, "agent_memory.sql")

PG_HOST = os.getenv("SCM_PG_HOST", "localhost")
PG_PORT = os.getenv("SCM_PG_PORT", "5432")
PG_USER = os.getenv("SCM_PG_USER", "gateway_user")
PG_PASSWORD = os.getenv("SCM_PG_PASSWORD", "gateway_pass")
PG_DATABASE = os.getenv("SCM_PG_DATABASE", "logistics_scm")


def apply(conn: psycopg.Connection) -> None:
    with open(PATH, encoding="utf-8") as fh:
        conn.execute(fh.read())
    if os.path.exists(MEMORY_PATH):
        with open(MEMORY_PATH, encoding="utf-8") as fh:
            conn.execute(fh.read())


def main() -> None:
    dsn = (
        f"host={PG_HOST} port={PG_PORT} user={PG_USER} "
        f"password={PG_PASSWORD} dbname={PG_DATABASE}"
    )
    with psycopg.connect(dsn, options="-c search_path=scm,public") as conn:
        apply(conn)
        conn.commit()
    print("execution ERP tables ready.")


if __name__ == "__main__":
    main()
