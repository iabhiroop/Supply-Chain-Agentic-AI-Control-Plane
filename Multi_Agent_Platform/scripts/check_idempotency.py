"""Verify that a redelivered event cannot double-apply business mutations.

plan.md §4.1 requires idempotent execution keyed by event identity: a duplicate
event must never book a second carrier or post a second stock adjustment.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.config.db import fetch_one  # noqa: E402
from agents.contracts import IncidentEvent  # noqa: E402
from agents.runtime import open_incident  # noqa: E402


async def main() -> int:
    # Start from a clean slate: a previous workflow run would already hold the
    # idempotency key for this event, so "first delivery" needs a reset to be
    # a meaningful assertion.
    subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "reset_scenario.py")],
        check=True, stdout=subprocess.DEVNULL,
    )

    event = IncidentEvent(
        event_type="STOCK_MISMATCH", order_id=10248, product_id=11,
        reported_qty=0, bin_id="A1-01", severity="HIGH",
    )

    before = fetch_one("SELECT COUNT(*) AS n FROM actions")["n"]

    first_id, first_created = open_incident(event)
    second_id, second_created = open_incident(event)
    third_id, third_created = open_incident(event.model_copy())

    after = fetch_one("SELECT COUNT(*) AS n FROM actions")["n"]
    incidents = fetch_one(
        "SELECT COUNT(*) AS n FROM incidents WHERE order_id = %s AND product_id = %s",
        (event.order_id, event.product_id),
    )["n"]

    checks = [
        ("first delivery opens an incident", first_created, f"incident {first_id[:8]}"),
        ("redelivery is recognised as a duplicate", not second_created,
         f"returned {second_id[:8]}, created={second_created}"),
        ("duplicate maps to the same incident", first_id == second_id == third_id,
         f"{first_id[:8]} / {second_id[:8]} / {third_id[:8]}"),
        ("no extra incident row was created", incidents == 1, f"{incidents} incident(s)"),
        ("no business mutation was replayed", before == after,
         f"actions before={before} after={after}"),
    ]

    ok = 0
    print("Idempotency (three deliveries of the same event):\n")
    for label, passed, detail in checks:
        ok += int(passed)
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
        print(f"       {detail}")

    print(f"\n{ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
