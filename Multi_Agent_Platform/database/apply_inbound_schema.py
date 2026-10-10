"""Apply inbound ASN/GR + detector tables without rebuilding logistics_scm."""

from __future__ import annotations

import os

import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "inbound_schema.sql")
CHAT_RO = os.path.join(HERE, "chat_ro.sql")

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
        conn.execute(open(PATH, encoding="utf-8").read())
        conn.execute(open(CHAT_RO, encoding="utf-8").read())
        conn.commit()
    print("inbound ASN/GR tables, detected_signals, and chat_ro views ready.")


if __name__ == "__main__":
    main()
