"""Run and verify the P2P System of Execution golden path (and delay exception).

Unattended happy path: PR-1001 → RFQs → legal winner → PO → booking →
DELIVERED → GR → invoices → four-way match → SETTLED, plus memory rows.

Delay path: PR-1002 3-day slip is within policy, so Logistics rebooks (no HITL).

HITL path is separate (`--hitl-only`): PR-1003 14-day slip exceeds `date_slip_days` (5).

Usage (from Multi_Agent_Platform/):
    python scripts/run_golden_transaction.py
    python scripts/run_golden_transaction.py --no-seed
    python scripts/run_golden_transaction.py --delay-only
    python scripts/run_golden_transaction.py --hitl-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.seed_execution import DELAY_TX, GOLDEN_TX, HITL_TX, seed_execution  # noqa: E402
from agents.config.db import fetch_all, fetch_one, get_conn  # noqa: E402
from agents.execution.runtime import execute_transaction  # noqa: E402
from agents.execution import services as exec_services  # noqa: E402
from database.apply_erp_execution import apply as apply_erp  # noqa: E402


def seed(jobs: tuple[str, ...] | None = None) -> None:
    with get_conn() as conn:
        apply_erp(conn)
        views = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "database",
            "chat_ro_execution.sql",
        )
        if os.path.exists(views):
            conn.execute(open(views, encoding="utf-8").read())
        seed_execution(conn, jobs=jobs)


def _check(checks: list[tuple[str, bool, str]], label: str, ok: bool, detail: str) -> None:
    checks.append((label, ok, detail))


def verify_golden() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    tx = fetch_one(
        "SELECT stage, status FROM execution_transactions WHERE transaction_id = %s",
        (GOLDEN_TX,),
    )
    _check(checks, "transaction SETTLED", bool(tx) and tx["stage"] == "SETTLED" and tx["status"] == "SETTLED",
           f"{tx}")

    quotes = fetch_all("SELECT vendor_id, unit_price FROM quotes WHERE transaction_id = %s", (GOLDEN_TX,))
    vendors = {q["vendor_id"] for q in quotes}
    _check(checks, "three vendor quotes", len(quotes) >= 3 and vendors >= {"VEND-A", "VEND-B", "VEND-C"},
           f"{quotes}")

    po = fetch_one("SELECT vendor_id, unit_price, quantity FROM exec_purchase_orders WHERE transaction_id = %s", (GOLDEN_TX,))
    _check(checks, "PO awarded to legal winner VEND-A @ 10.20",
           bool(po) and po["vendor_id"] == "VEND-A" and float(po["unit_price"]) == 10.20 and po["quantity"] == 20,
           f"{po}")

    rogue = fetch_one(
        "SELECT unit_price FROM quotes WHERE transaction_id = %s AND vendor_id = 'VEND-C'",
        (GOLDEN_TX,),
    )
    _check(checks, "rogue +12% bid stored but not awarded",
           bool(rogue) and float(rogue["unit_price"]) == 11.20 and po is not None and po["vendor_id"] != "VEND-C",
           f"rogue={rogue} po_vendor={(po or {}).get('vendor_id')}")

    book = fetch_one("SELECT booking_id, carrier_name, status FROM bookings WHERE transaction_id = %s", (GOLDEN_TX,))
    _check(checks, "contracted carrier booked",
           bool(book) and book["carrier_name"] == "Speedy Express", f"{book}")

    delivered = fetch_one(
        "SELECT COUNT(*) AS n FROM tracking_events WHERE transaction_id = %s AND event_code = 'DELIVERED'",
        (GOLDEN_TX,),
    )
    _check(checks, "tracking reached DELIVERED", bool(delivered) and delivered["n"] >= 1, f"{delivered}")

    gr = fetch_one("SELECT quantity FROM goods_receipts_exec WHERE transaction_id = %s", (GOLDEN_TX,))
    _check(checks, "GR posted at counted qty 20", bool(gr) and gr["quantity"] == 20, f"{gr}")

    match = fetch_one(
        "SELECT status, variance_pct FROM invoice_matches WHERE transaction_id = %s",
        (GOLDEN_TX,),
    )
    _check(checks, "four-way match posted",
           bool(match) and match["status"] == "POSTED" and float(match["variance_pct"]) <= 5,
           f"{match}")

    mem_s = fetch_one("SELECT COUNT(*) AS n FROM supplier_memory WHERE transaction_id = %s", (GOLDEN_TX,))
    mem_l = fetch_one("SELECT COUNT(*) AS n FROM lane_memory WHERE transaction_id = %s", (GOLDEN_TX,))
    mem_c = fetch_one("SELECT COUNT(*) AS n FROM commercial_memory WHERE transaction_id = %s AND paid_price IS NOT NULL", (GOLDEN_TX,))
    _check(checks, "operational memory rows (supplier, lane, commercial)",
           bool(mem_s and mem_l and mem_c) and mem_s["n"] >= 1 and mem_l["n"] >= 1 and mem_c["n"] >= 1,
           f"supplier={mem_s} lane={mem_l} commercial={mem_c}")
    return checks


def verify_delay() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    tx = fetch_one(
        "SELECT stage, status, context FROM execution_transactions WHERE transaction_id = %s",
        (DELAY_TX,),
    )
    ctx = (tx or {}).get("context") or {}
    _check(checks, "delay recovered without HITL",
           bool(tx) and tx["status"] != "AWAITING_HUMAN" and (ctx.get("rebooked") or tx["status"] == "OPEN"),
           f"{tx}")
    book = fetch_one("SELECT status FROM bookings WHERE transaction_id = %s", (DELAY_TX,))
    _check(checks, "booking rebooked within slip",
           bool(book) and book["status"] == "REBOOKED", f"{book}")
    mem = fetch_one(
        "SELECT recovery_action, effective FROM exception_memory WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1",
        (DELAY_TX,),
    )
    _check(checks, "exception memory records rebook",
           bool(mem) and mem["recovery_action"] == "rebook_within_slip" and mem["effective"] is True,
           f"{mem}")
    return checks


def verify_hitl() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    tx = fetch_one(
        "SELECT stage, status, halt_reason FROM execution_transactions WHERE transaction_id = %s",
        (HITL_TX,),
    )
    _check(
        checks,
        "14-day slip waits for human",
        bool(tx) and tx["status"] == "AWAITING_HUMAN" and tx["stage"] == "AWAITING_HUMAN",
        f"{tx}",
    )
    reason = (tx or {}).get("halt_reason") or ""
    _check(checks, "halt reason mentions 14-day slip", "14" in reason, reason)
    book = fetch_one("SELECT status FROM bookings WHERE transaction_id = %s", (HITL_TX,))
    _check(
        checks,
        "booking not rebooked",
        bool(book) and book["status"] != "REBOOKED",
        f"{book}",
    )
    return checks


def verify_hitl_resume() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    tx = fetch_one(
        "SELECT stage, status, halt_reason, context FROM execution_transactions WHERE transaction_id = %s",
        (HITL_TX,),
    )
    ctx = (tx or {}).get("context") or {}
    _check(
        checks,
        "human exception rebooked",
        bool(tx) and tx["status"] != "AWAITING_HUMAN" and (ctx.get("rebooked") or tx["status"] == "OPEN"),
        f"{tx}",
    )
    book = fetch_one("SELECT status FROM bookings WHERE transaction_id = %s", (HITL_TX,))
    _check(checks, "booking REBOOKED after HITL", bool(book) and book["status"] == "REBOOKED", f"{book}")
    mem = fetch_one(
        "SELECT recovery_action, approved, effective FROM exception_memory WHERE transaction_id = %s ORDER BY created_at DESC LIMIT 1",
        (HITL_TX,),
    )
    _check(
        checks,
        "exception memory records approved rebook",
        bool(mem) and mem["approved"] is True and mem["effective"] is True,
        f"{mem}",
    )
    return checks


def report(title: str, checks: list[tuple[str, bool, str]]) -> int:
    print(f"\n{title}")
    failed = 0
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
        if not ok:
            failed += 1
    return failed


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--delay-only", action="store_true")
    parser.add_argument("--hitl-only", action="store_true")
    parser.add_argument("--skip-delay", action="store_true")
    parser.add_argument(
        "--hold",
        action="store_true",
        help="With --hitl-only: stop at AWAITING_HUMAN (do not decide/resume).",
    )
    args = parser.parse_args()

    if not args.no_seed:
        if args.hitl_only:
            print("Seeding PR-1003 only (does not reset PR-1001 / PR-1002)…")
            seed(jobs=("hitl",))
        elif args.delay_only:
            print("Seeding PR-1002 only (does not reset PR-1001 / PR-1003)…")
            seed(jobs=("delay",))
        else:
            print("Seeding execution ERP (PR-1001 / PR-1002 / PR-1003)…")
            seed()

    failed = 0
    if args.hitl_only:
        print("Running HITL delay PR-1003…")
        result = await execute_transaction(HITL_TX)
        print(f"  status={result.get('status')} stage={result.get('stage')} "
              f"elapsed={result.get('elapsed_seconds')}s error={result.get('error')} "
              f"trace_id={result.get('trace_id')}")
        failed += report("HITL delay checks", verify_hitl())
        if args.hold:
            print("Holding at AWAITING_HUMAN (--hold).")
            print("\nDone." if failed == 0 else f"\n{failed} check(s) failed.")
            return 0 if failed == 0 else 1
        print("Human decide: approve_exception + resume…")
        decided = exec_services.apply_human_decision(HITL_TX, "approve_exception")
        print(f"  decision={decided}")
        if decided.get("resumed"):
            result = await execute_transaction(HITL_TX)
            print(f"  status={result.get('status')} stage={result.get('stage')} "
                  f"elapsed={result.get('elapsed_seconds')}s")
        failed += report("HITL resume checks", verify_hitl_resume())
        print("\nDone." if failed == 0 else f"\n{failed} check(s) failed.")
        return 0 if failed == 0 else 1

    if not args.delay_only:
        print("Running golden transaction PR-1001…")
        result = await execute_transaction(GOLDEN_TX)
        print(f"  status={result.get('status')} stage={result.get('stage')} "
              f"elapsed={result.get('elapsed_seconds')}s error={result.get('error')}")
        if result.get("error"):
            print(f"  halt={result.get('halt_reason')}")
        failed += report("Golden path checks", verify_golden())

    if not args.skip_delay:
        print("Running delay exception PR-1002…")
        result = await execute_transaction(DELAY_TX)
        print(f"  status={result.get('status')} stage={result.get('stage')} "
              f"elapsed={result.get('elapsed_seconds')}s error={result.get('error')}")
        failed += report("Delay exception checks", verify_delay())

    print("\nDone." if failed == 0 else f"\n{failed} check(s) failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
