"""Apply additive chat-layer tables without rebuilding logistics_scm."""

from __future__ import annotations

import os

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = [
    os.path.join(HERE, "inbound_schema.sql"),
    os.path.join(HERE, "chat_schema.sql"),
    os.path.join(HERE, "chat_ro.sql"),
    os.path.join(HERE, "erp_execution.sql"),
    os.path.join(HERE, "chat_ro_execution.sql"),
]

PG_HOST = os.getenv("SCM_PG_HOST", "localhost")
PG_PORT = os.getenv("SCM_PG_PORT", "5432")
PG_USER = os.getenv("SCM_PG_USER", "gateway_user")
PG_PASSWORD = os.getenv("SCM_PG_PASSWORD", "gateway_pass")
PG_DATABASE = os.getenv("SCM_PG_DATABASE", "logistics_scm")


def main() -> None:
    dsn = (
        f"host={PG_HOST} port={PG_PORT} user={PG_USER} "
        f"password={PG_PASSWORD} dbname={PG_DATABASE}"
    )
    with psycopg.connect(dsn) as conn:
        for path in FILES:
            conn.execute(open(path, encoding="utf-8").read())
        conn.commit()
    print("chat tables, chat_ro views, and scm_chat_reader ready.")


if __name__ == "__main__":
    main()
