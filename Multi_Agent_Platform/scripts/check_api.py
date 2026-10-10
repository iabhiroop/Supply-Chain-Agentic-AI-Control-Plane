"""Smoke-test every internal API endpoint the agent tools depend on.

Read-only checks run first, then the two mutating endpoints, whose effects are
reverted at the end so the reference scenario stays reproducible.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.config.settings import settings  # noqa: E402

BASE = settings.api_base_url
ORDER_ID = 10248
PRODUCT_ID = 11
BIN_ID = "A1-01"

passed = failed = 0


def call(method: str, path: str, body: dict | None = None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:200]


def check(label: str, method: str, path: str, body=None, expect=200, show=None):
    global passed, failed
    status, payload = call(method, path, body)
    ok = status == expect
    passed, failed = passed + int(ok), failed + int(not ok)
    detail = ""
    if ok and show:
        try:
            detail = show(payload)
        except Exception as exc:  # noqa: BLE001
            detail = f"(could not summarise: {exc})"
    elif not ok:
        detail = f"got {status}: {payload}"
    print(f"[{'PASS' if ok else 'FAIL'}] {label:44} {detail}")
    return payload


print(f"Internal API: {BASE}\n")
print("--- read-only ---")

check("GET bin stock (product 11)", "GET", f"/inventory/bin/{PRODUCT_ID}",
      show=lambda p: f"{len(p)} bin(s); {p[0]['bin_id']}={p[0]['quantity']} ({p[0]['product_name']})")

check("GET stock ledger (product 11)", "GET", f"/inventory/ledger/{PRODUCT_ID}",
      show=lambda p: f"{len(p)} entries; latest={p[0]['transaction_type']} {p[0]['quantity']:+d}")

check("GET shipment (order 10248)", "GET", f"/shipments/{ORDER_ID}",
      show=lambda p: (f"carrier={p['carrier_name']} status={p['status']} "
                      f"days_late={p['days_late']}"))

check("GET carrier rates (France)", "GET", "/carrier_rates/France",
      show=lambda p: "; ".join(
          f"{r['carrier_name']}={r['expedited_rate']}/{r['expedited_transit_days']}d"
          f"{'' if r['capacity_available'] else ' NO-CAP'}" for r in p))

check("GET carrier rates (unknown region)", "GET", "/carrier_rates/Atlantis", expect=404)

check("GET customer (order 10248)", "GET", f"/customers/{ORDER_ID}",
      show=lambda p: f"{p['company_name']} <{p['email']}> {p['ship_country']}")

check("GET SLA terms (order 10248)", "GET", f"/customers/{ORDER_ID}/sla",
      show=lambda p: "; ".join(f"{t['metric']}<={t['threshold_days']}d" for t in p))

check("GET order lines (order 10248)", "GET", f"/orders/{ORDER_ID}/lines",
      show=lambda p: "; ".join(f"{l['product_name']} x{l['quantity']}" for l in p))

print("\n--- guardrails (must be refused) ---")
check("tender to carrier without capacity", "POST", "/shipments/tender",
      {"order_id": ORDER_ID, "new_carrier_id": 3, "new_estimated_delivery": "2026-09-08"},
      expect=409)
check("tender to carrier not serving region", "POST", "/shipments/tender",
      {"order_id": ORDER_ID, "new_carrier_id": 4, "new_estimated_delivery": "2026-09-08"},
      expect=409)
check("tender to nonexistent carrier", "POST", "/shipments/tender",
      {"order_id": ORDER_ID, "new_carrier_id": 999, "new_estimated_delivery": "2026-09-08"},
      expect=404)
check("adjust nonexistent bin", "POST", "/inventory/adjust",
      {"product_id": PRODUCT_ID, "bin_id": "Z9-99", "new_quantity": 0, "reason": "test"},
      expect=404)

print("\n--- mutations (reverted afterwards) ---")
adj = check("POST adjust product 11 to 0", "POST", "/inventory/adjust",
            {"product_id": PRODUCT_ID, "bin_id": BIN_ID, "new_quantity": 0,
             "reason": "Smoke test: physical bin empty"},
            show=lambda p: (f"{p['previous_quantity']}->{p['new_quantity']} "
                            f"(delta {p['delta']:+d}, on_hand {p['units_in_stock_after']}) "
                            f"action={p['action_id'][:8]}"))

check("POST adjust again (no-op refused)", "POST", "/inventory/adjust",
      {"product_id": PRODUCT_ID, "bin_id": BIN_ID, "new_quantity": 0, "reason": "again"},
      expect=409)

tender = check("POST tender to Speedy Express", "POST", "/shipments/tender",
               {"order_id": ORDER_ID, "new_carrier_id": 1,
                "new_estimated_delivery": "2026-09-08 12:00:00",
                "reason": "Smoke test re-tender"},
               show=lambda p: (f"{p['previous_carrier_name']}->{p['new_carrier_name']} "
                               f"cost={p['freight_cost']} meets_promise={p['meets_promise']}"))

check("POST log customer email", "POST", "/communications/email",
      {"to_email": "paul.henriot@vins.et.alcools.chevalier.example.com",
       "subject": "Smoke test", "body": "Test body", "order_id": ORDER_ID},
      show=lambda p: f"communication_id={p['communication_id']}")

print("\n--- reverting via recorded inverse actions ---")
if isinstance(adj, dict) and adj.get("status") == "success":
    check("revert inventory adjustment", "POST", "/inventory/adjust",
          {"product_id": PRODUCT_ID, "bin_id": BIN_ID,
           "new_quantity": adj["previous_quantity"], "reason": "Revert smoke test"},
          show=lambda p: f"restored to {p['new_quantity']}")

if isinstance(tender, dict) and tender.get("status") == "success":
    # is_compensation=True: the original carrier dropped the load and has no
    # capacity, so a plain forward booking would (correctly) be refused.
    check("revert shipment tender (compensation)", "POST", "/shipments/tender",
          {"order_id": ORDER_ID, "new_carrier_id": tender["previous_carrier_id"],
           "new_estimated_delivery": "2026-09-12 08:00:00",
           "service_level": "STANDARD", "reason": "Revert smoke test",
           "is_compensation": True},
          show=lambda p: f"restored to {p['new_carrier_name']}")

print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
