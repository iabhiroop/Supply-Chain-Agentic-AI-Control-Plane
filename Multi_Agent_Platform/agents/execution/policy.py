"""Python policy functions for the P2P execution spine.

Price math, budget, approval thresholds, and date-slip checks live here — not
in the LLM. The model may only choose among options this module already marked
legal.
"""

from __future__ import annotations

from typing import Any


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def deviation_pct(actual: Any, baseline: Any) -> float:
    base = as_float(baseline)
    if base == 0:
        return 0.0
    return abs(as_float(actual) - base) / base * 100.0


def quote_is_legal(unit_price: Any, last_price: Any, max_pct: Any) -> bool:
    return deviation_pct(unit_price, last_price) <= as_float(max_pct) + 1e-9


def legal_quotes(quotes: list[dict], last_price: Any, max_pct: Any) -> list[dict]:
    return [q for q in quotes if quote_is_legal(q.get("unit_price"), last_price, max_pct)]


def pick_winner(quotes: list[dict]) -> dict | None:
    if not quotes:
        return None
    return sorted(
        quotes,
        key=lambda q: (as_float(q.get("unit_price")), int(q.get("lead_days") or 99)),
    )[0]


def po_amount(quantity: Any, unit_price: Any) -> float:
    return as_float(quantity) * as_float(unit_price)


def needs_approval(
    amount: float,
    policy: dict,
    rules: list[dict],
    cost_center: str,
) -> bool:
    threshold = as_float(policy.get("approval_amount_usd"), 5000)
    if amount >= threshold:
        return True
    for rule in rules:
        if rule.get("cost_center") and rule["cost_center"] != cost_center:
            continue
        if amount >= as_float(rule.get("min_amount")) and rule.get("must_hitl"):
            return True
    return False


def within_budget(amount: float, budget: Any) -> bool:
    return amount <= as_float(budget) + 1e-9


def slip_within_tolerance(slip_days: Any, policy: dict) -> bool:
    return int(slip_days or 0) <= int(policy.get("date_slip_days") or 0)


def delay_recoveries(
    slip_days: Any,
    policy: dict,
    *,
    similar: list[dict] | None = None,
    human_approved: bool = False,
) -> list[str]:
    """Legal recovery options for a supplier delay. Empty → must HITL."""
    if human_approved:
        return ["rebook"]
    if not slip_within_tolerance(slip_days, policy):
        return []
    if graph_requires_hitl(similar or [], slip_days):
        return []
    return ["rebook"]


def graph_requires_hitl(similar: list[dict], slip_days: Any) -> bool:
    """Hold when a prior incident at this slip (or less) recovered with HITL.

    A 3-day slip is not forced to hold just because a 14-day playbook exists.
    """
    current = int(slip_days or 0)
    for inc in similar or []:
        rec = f"{inc.get('recovery') or ''} {inc.get('action') or ''} {inc.get('decision') or ''}"
        if "AWAITING_HUMAN" not in rec.upper() and "hold" not in rec.lower():
            continue
        past = inc.get("slip_days")
        try:
            past_n = int(past) if past is not None and str(past) != "" else None
        except (TypeError, ValueError):
            past_n = None
        if past_n is None:
            continue
        if current >= past_n:
            return True
    return False


def match_variance_pct(
    *,
    po_price: Any,
    invoice_price: Any,
    po_qty: Any,
    gr_qty: Any,
    invoice_qty: Any,
    booked_freight: Any,
    freight_amount: Any,
) -> float:
    qty_base = as_float(po_qty)
    qty_var = 0.0 if qty_base == 0 else abs(as_float(gr_qty) - qty_base) / qty_base * 100
    inv_qty_var = 0.0 if qty_base == 0 else abs(as_float(invoice_qty) - qty_base) / qty_base * 100
    return max(
        deviation_pct(invoice_price, po_price),
        qty_var,
        inv_qty_var,
        deviation_pct(freight_amount, booked_freight),
    )


def pr_is_complete(pr: dict | None) -> tuple[bool, list[str]]:
    if not pr:
        return False, ["purchase requisition missing"]
    missing = []
    for field in ("material_id", "quantity", "plant_id", "cost_center", "need_by"):
        if pr.get(field) in (None, ""):
            missing.append(field)
    if int(pr.get("quantity") or 0) <= 0:
        missing.append("quantity")
    return (not missing), missing
