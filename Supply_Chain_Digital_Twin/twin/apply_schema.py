"""Apply twin schema (and Layer 2 ERP tables if plants is missing)."""

from __future__ import annotations

from pathlib import Path

import psycopg

from twin.config import settings
from twin.db import scm_conn

_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = _ROOT / "schema.sql"
ERP_SQL = Path(settings.layer2_root) / "database" / "erp_execution.sql"


def _table_exists(conn: psycopg.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'scm' AND table_name = %s LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def apply(conn: psycopg.Connection | None = None) -> None:
    def _run(c: psycopg.Connection) -> None:
        c.execute("CREATE SCHEMA IF NOT EXISTS scm")
        if not _table_exists(c, "plants"):
            if not ERP_SQL.is_file():
                raise RuntimeError(
                    "scm.plants is missing and Layer 2 erp_execution.sql was not found. "
                    f"Expected {ERP_SQL}"
                )
            try:
                c.execute(ERP_SQL.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(
                    "scm.plants is missing. Create logistics_scm first "
                    "(Multi_Agent_Platform/database/build_database.py), then retry."
                ) from exc
        c.execute(SCHEMA.read_text(encoding="utf-8"))

    if conn is not None:
        _run(conn)
        return
    with scm_conn() as c:
        _run(c)


def main() -> None:
    apply()
    print("twin schema ready.")


if __name__ == "__main__":
    main()
