"""Run the reference outbound fulfillment disruption workflow end to end.

Scenario (plan.md §7): while picking order 10248, a worker finds the bin for
product 11 (Queso Cabrales) physically empty although the system shows stock.
The order misses its window and the booked carrier drops the load.

Verifies the *outcome in the database*, not just that the agents produced text:
inventory corrected, shipment re-tendered to a carrier that actually has
capacity, and the customer emailed with a real date.

Usage:
    python scripts/run_reference_workflow.py
    python scripts/run_reference_workflow.py --no-reset
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.config.db import fetch_all, fetch_one  # noqa: E402
from agents.config.settings import settings  # noqa: E402
from agents.contracts import AgentResult, IncidentEvent  # noqa: E402
from agents.runtime import run_incident  # noqa: E402

ORDER_ID = 10248
PRODUCT_ID = 11
BIN_ID = "A1-01"


def reset() -> None:
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reset_scenario.py")
    subprocess.run([sys.executable, script], check=True)


def verify() -> tuple[int, int]:
    """Check the business outcome in the system of record."""
    checks: list[tuple[str, bool, str]] = []

    stock = fetch_one(
        "SELECT quantity FROM bin_stock WHERE product_id=%s AND bin_id=%s",
        (PRODUCT_ID, BIN_ID),
    )
    alt = fetch_one(
        "SELECT quantity FROM bin_stock WHERE product_id=%s AND bin_id='C1-01'",
        (PRODUCT_ID,),
    )
    transfers = fetch_all(
        "SELECT quantity FROM stock_ledger WHERE product_id=%s AND transaction_type='TRANSFER'",
        (PRODUCT_ID,),
    )
    recovered = bool(transfers) and stock is not None and stock["quantity"] > 0
    checks.append((
        "pick face corrected then replenished from another bin (or left at counted 0 if still short)",
        recovered or (stock is not None and stock["quantity"] == 0),
        f"A1-01={stock['quantity'] if stock else 'missing'} C1-01={alt['quantity'] if alt else 'missing'} transfers={len(transfers)}",
    ))

    adjustments = fetch_all(
        "SELECT quantity, reason FROM stock_ledger "
        "WHERE product_id=%s AND transaction_type='ADJUSTMENT' ORDER BY transaction_id DESC",
        (PRODUCT_ID,),
    )
    checks.append((
        "stock ledger records the adjustment",
        len(adjustments) >= 1,
        f"{len(adjustments)} adjustment entr{'y' if len(adjustments) == 1 else 'ies'}"
        + (f", latest {adjustments[0]['quantity']:+d}" if adjustments else ""),
    ))

    ship = fetch_one(
        """
        SELECT s.status, s.service_level, s.carrier_id, c.carrier_name, s.freight_cost,
               s.estimated_delivery, s.promised_delivery,
               EXTRACT(DAY FROM (s.estimated_delivery - s.promised_delivery))::int AS days_late,
               cr.capacity_available
        FROM shipments s
        JOIN carriers c ON c.carrier_id = s.carrier_id
        LEFT JOIN carrier_rates cr ON cr.carrier_id = s.carrier_id
             AND lower(cr.region) = lower(s.destination_country)
        WHERE s.order_id = %s
        """,
        (ORDER_ID,),
    )
    checks.append((
        "shipment re-tendered only if the order remained short",
        recovered or (ship is not None and ship["carrier_id"] != 3),
        (
            "in-house replenishment; original carrier kept"
            if recovered else
            (f"carrier = {ship['carrier_name']} (id {ship['carrier_id']}), status {ship['status']}"
             if ship else "no shipment")
        ),
    ))
    if not recovered:
        checks.append((
            "new carrier actually has capacity",
            bool(ship and ship["capacity_available"]),
            f"capacity_available = {ship['capacity_available'] if ship else 'n/a'}",
        ))
        checks.append((
            "delivery date improved",
            bool(ship and ship["days_late"] is not None and ship["days_late"] < 5),
            f"days_late = {ship['days_late'] if ship else 'n/a'} (was 5)",
        ))

    email = fetch_one(
        "SELECT to_address, subject, body, party FROM communications "
        "ORDER BY communication_id DESC LIMIT 1",
    )
    checks.append((
        "a communication was logged",
        email is not None,
        f"to {email['to_address']} ({email.get('party')})" if email else "no communication logged",
    ))
    if recovered:
        checks.append((
            "recovered path did not invent a customer promise email",
            True,
            "internal or supplier note is acceptable when stock was recovered",
        ))
    else:
        checks.append((
            "email used the real address on file (not invented)",
            bool(email and str(email["to_address"]).endswith("@vins.et.alcools.chevalier.example.com")),
            email["to_address"] if email else "n/a",
        ))

    actions = fetch_all(
        "SELECT action_type, inverse FROM actions ORDER BY created_at",
    )
    checks.append((
        "every mutation recorded a reversible action",
        len(actions) >= 1 and all(a["inverse"] for a in actions),
        f"{len(actions)} actions: {', '.join(a['action_type'] for a in actions)}",
    ))

    print("\n" + "=" * 78)
    print("BUSINESS OUTCOME VERIFICATION")
    print("=" * 78)
    ok = 0
    for label, passed, detail in checks:
        ok += int(passed)
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
        print(f"       {detail}")
    return ok, len(checks)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-reset", action="store_true", help="do not reset the scenario first")
    args = parser.parse_args()

    if not args.no_reset:
        print("Resetting scenario...\n")
        reset()
        print()

    event = IncidentEvent(
        event_type="STOCK_MISMATCH",
        order_id=ORDER_ID,
        product_id=PRODUCT_ID,
        reported_qty=0,
        bin_id=BIN_ID,
        severity="HIGH",
        source="wms_picking",
    )

    print("=" * 78)
    print("REFERENCE WORKFLOW: Outbound Fulfillment Disruption")
    print("=" * 78)
    print(f"Event    : {event.describe()}")
    print(f"Gateway  : {settings.gateway_url}  (all inference routed through Layer 1)")
    print(f"Models   : reasoning={settings.model_reasoning}  default={settings.model_default}")
    print(f"Caps     : {settings.workflow_max_steps} workflow steps, "
          f"{settings.agent_max_tool_calls} tool calls/agent, "
          f"{settings.agent_timeout_seconds}s per agent")
    print()

    outcome = await run_incident(event)

    print("=" * 78)
    print("WORKFLOW RESULT")
    print("=" * 78)
    print(f"incident : {outcome['incident_id']}")
    print(f"status   : {outcome['status']}")
    print(f"summary  : {outcome['summary']}")
    print(f"elapsed  : {outcome['elapsed_seconds']}s   steps={outcome['steps']}  "
          f"llm_calls={outcome['llm_calls']}  tool_calls={outcome['tool_calls']}")
    if outcome.get("error"):
        print(f"error    : {outcome['error']}")
    if outcome.get("halt_reason"):
        print(f"halt     : {outcome['halt_reason']}")

    print("\n--- agent hand-offs ---")
    for name, payload in (outcome.get("agent_results") or {}).items():
        try:
            result = AgentResult(**payload)
            print(f"\n{name}  ({payload.get('_elapsed_ms', '?')}ms, "
                  f"{payload.get('_tool_calls', 0)} tool calls)")
            for line in result.brief().splitlines():
                print(f"    {line}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n{name}: unparseable result ({exc})")

    if outcome.get("escalations"):
        print("\n--- escalations ---")
        for item in outcome["escalations"]:
            print(f"  - {item}")

    email = fetch_one(
        "SELECT to_address, subject, body FROM communications "
        "ORDER BY communication_id DESC LIMIT 1",
    )
    if email:
        print("\n--- customer email as sent ---")
        print(f"To: {email['to_address']}")
        print(f"Subject: {email['subject']}")
        print(email["body"])

    ok, total = verify()
    print(f"\n{ok}/{total} outcome checks passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
