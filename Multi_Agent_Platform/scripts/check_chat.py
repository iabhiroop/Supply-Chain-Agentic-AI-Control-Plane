"""Smoke-test the chat layer without running the full ~95s workflow.

Requires the internal API on port 8001 and chat tables applied.
Does not confirm a STOCK_MISMATCH card (that would mutate the scenario).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.config.db import fetch_one  # noqa: E402
from agents.config.settings import settings  # noqa: E402

ROOT = settings.api_base_url.rsplit("/api", 1)[0]
passed = failed = 0


def call_json(method: str, path: str, body: dict | None = None, timeout: int = 180):
    url = f"{ROOT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400]


def call_text(path: str):
    url = f"{ROOT}{path}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read().decode()


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {label}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"[FAIL] {label}" + (f"  {detail}" if detail else ""))


def grounded(reply: str) -> bool:
    blob = reply.lower()
    return any(token in blob for token in (
        "10248", "vinet", "speedy", "federal", "sla", "shipment",
        "chevalier", "france", "queso",
    ))


def main() -> int:
    status, health = call_json("GET", "/health")
    check(status == 200 and isinstance(health, dict) and health.get("status") == "healthy",
          "API health", str(health)[:120])

    try:
        status, html = call_text("/dashboard")
        check(status == 200 and "agent command" in html.lower() and "execution copilot" in html.lower(),
              "dashboard HTML")
    except Exception as exc:  # noqa: BLE001
        check(False, "dashboard HTML", str(exc))

    status, overview = call_json("GET", "/v1/ops/overview")
    check(status == 200 and isinstance(overview, dict) and "kpis" in overview, "ops overview")
    if status == 200:
        check((overview.get("reference") or {}).get("order_id") == 10248,
              "reference order on overview",
              str((overview.get("reference") or {}).get("order_id")))
        check(bool(overview.get("scenarios")), "playbooks present")

    status, ask = call_json("POST", "/v1/chat", {
        "message": "How many units of product 11 are in bin A1-01?",
        "user_id": "check_chat",
        "user_role": "warehouse_worker",
    })
    check(status == 200, "ask turn HTTP 200", str(status) if status != 200 else "")
    if status != 200:
        print(ask)
        print(f"\n{passed} passed, {failed} failed")
        return 1

    check(ask.get("intent") == "ask", "ask intent", str(ask.get("intent")))
    check(bool(ask.get("reply")), "ask produced a reply")
    reply = ask.get("reply") or ""
    stock = fetch_one(
        "SELECT quantity FROM scm.bin_stock WHERE product_id = 11 AND bin_id = 'A1-01'"
    )
    want = str(stock["quantity"]) if stock else "20"
    check(want in reply, f"ask reply contains on-hand quantity {want}", reply[:220])
    cards = ask.get("cards") or []
    qcard = next((c for c in cards if c.get("kind") == "query"), None)
    check(bool(qcard), "ask includes query evidence card")
    if qcard:
        sql = (qcard.get("sql") or "").lower()
        check("chat_ro" in sql or "v_bin_stock" in sql, "query card SQL hits chat_ro", sql[:180])
    session_id = ask["session_id"]

    status, propose = call_json("POST", "/v1/chat", {
        "session_id": session_id,
        "message": "Stock mismatch: bin A1-01 is empty for Queso Cabrales on order 10248, counted 0.",
    })
    check(status == 200, "propose turn HTTP 200")
    if status != 200:
        print(propose)
        print(f"\n{passed} passed, {failed} failed")
        return 1

    check(propose.get("intent") == "propose_event", "propose intent", str(propose.get("intent")))
    check(bool(propose.get("pending_proposal_id")), "event card issued")
    cards = propose.get("cards") or []
    check(bool(cards) and cards[0].get("kind") == "event", "card kind is event")
    event = (cards[0].get("event") or {}) if cards else {}
    check(event.get("event_type") == "STOCK_MISMATCH", "event type STOCK_MISMATCH")
    check(event.get("order_id") == 10248, "resolved order 10248", str(event.get("order_id")))
    check(event.get("product_id") == 11, "resolved product 11", str(event.get("product_id")))
    check(event.get("reported_qty") == 0, "reported qty 0")
    proposal_id = propose.get("pending_proposal_id")

    status, cancel = call_json("POST", "/v1/chat", {
        "session_id": session_id,
        "message": "no",
        "cancel_proposal_id": proposal_id,
    })
    check(status == 200 and isinstance(cancel, dict) and cancel.get("intent") == "cancel",
          "cancel pending card")

    status, sess = call_json("GET", f"/v1/chat/sessions/{session_id}")
    check(status == 200, "get session")
    if status == 200:
        check(sess["session"].get("pending_proposal") in (None, {}), "pending cleared after cancel")
        check(len(sess.get("messages") or []) >= 6, "messages persisted",
              str(len(sess.get("messages") or [])))

    status, sla = call_json("POST", "/v1/chat", {
        "message": "SELECT * FROM chat_ro.v_sla_terms",
        "user_id": "check_chat",
        "user_role": "warehouse_worker",
        "session_id": session_id,
    })
    # Natural-language ask still goes through the model; worker SQL against SLA is
    # covered in check_sql_sandbox.py. Here we only require the ask path to return.
    check(status == 200, "worker ask still HTTP 200")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
