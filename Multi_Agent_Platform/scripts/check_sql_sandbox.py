"""Unit checks for the SELECT-only warehouse sandbox (no LLM required)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.chat.sql_sandbox import run_readonly, validate_sql  # noqa: E402
from agents.config.db import fetch_one  # noqa: E402

passed = failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {label}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {label}" + (f"  {detail}" if detail else ""))


def main() -> int:
    role_w = "warehouse_worker"
    role_m = "warehouse_manager"

    sql, err = validate_sql(
        "UPDATE scm.bin_stock SET quantity = 0 WHERE product_id = 11", role_w
    )
    check(sql is None and err, "reject UPDATE", err or "")

    sql, err = validate_sql("SELECT * FROM scm.customers", role_m)
    check(sql is None and err, "reject scm.customers", err or "")

    sql, err = validate_sql(
        "SELECT 1; SELECT * FROM chat_ro.v_bin_stock", role_w
    )
    check(sql is None and err, "reject stacked statements", err or "")

    sql, err = validate_sql(
        "SELECT bin_id, quantity FROM chat_ro.v_bin_stock WHERE product_id = 11",
        role_w,
    )
    check(sql is not None and err is None, "accept worker bin stock SELECT", sql or "")
    if sql:
        check("limit" in sql.lower(), "forces LIMIT", sql)

    sql, err = validate_sql(
        "SELECT * FROM chat_ro.v_sla_terms", role_w
    )
    check(sql is None and err, "worker cannot query v_sla_terms", err or "")

    sql, err = validate_sql(
        "SELECT asn_id, expected_qty FROM chat_ro.v_inbound_asns LIMIT 5", role_w
    )
    check(sql is None and err, "worker cannot query v_inbound_asns", err or "")

    sql, err = validate_sql(
        "SELECT customer_id, metric FROM chat_ro.v_sla_terms LIMIT 5", role_m
    )
    check(sql is not None and err is None, "manager can query v_sla_terms")

    result = run_readonly(
        "SELECT bin_id, quantity FROM chat_ro.v_bin_stock WHERE product_id = 11 AND bin_id = 'A1-01'",
        role_w,
    )
    check(result["ok"], "execute bin stock", result.get("error") or "")
    if result["ok"]:
        expected = fetch_one(
            "SELECT quantity FROM scm.bin_stock WHERE product_id = 11 AND bin_id = 'A1-01'"
        )
        qty = result["rows"][0]["quantity"] if result["rows"] else None
        want = expected["quantity"] if expected else None
        check(qty == want, f"sandbox qty matches system of record ({want})", str(qty))
        check("chat_ro" in (result.get("sql") or "").lower(), "executed SQL is chat_ro")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
