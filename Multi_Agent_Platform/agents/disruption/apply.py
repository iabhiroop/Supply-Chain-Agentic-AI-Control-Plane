"""HITL apply of a disruption reallocation. Inverse restores PO-1842 qty."""

from __future__ import annotations

from typing import Any

from agents.config.db import get_conn
from api.database.db import record_action


def apply_reallocation(recommendation: dict[str, Any], *, decision: str = "approve") -> dict[str, Any]:
    action = (decision or "").strip().lower()
    realloc = (recommendation or {}).get("reallocate") or {}
    po_id = realloc.get("po_id")
    qty = int(float(realloc.get("qty") or 0))
    if action in {"reject", "keep_waiting", "hold"}:
        return {"ok": True, "applied": False, "decision": action, "po_id": po_id}
    if action not in {"approve", "approve_exception", "apply"}:
        return {"error": True, "detail": f"Unknown decision {decision}"}
    if not po_id or qty <= 0:
        return {"error": True, "detail": "Recommendation has no reallocatable qty"}

    with get_conn() as conn:
        row = conn.execute(
            "SELECT po_id, quantity, reallocatable_qty, status FROM twin_purchase_orders WHERE po_id = %s",
            (po_id,),
        ).fetchone()
        if not row:
            return {"error": True, "detail": f"Unknown PO {po_id}"}
        prev_qty = int(row["quantity"])
        prev_realloc = int(row["reallocatable_qty"])
        if qty > prev_qty:
            return {"error": True, "detail": f"{po_id} qty {prev_qty} cannot move {qty}"}
        if qty > prev_realloc:
            prior = None
            try:
                from agents.memory.store import already_applied

                prior = already_applied(po_id)
            except Exception:  # noqa: BLE001
                prior = None
            return {
                "ok": True,
                "applied": False,
                "already_applied": True,
                "po_id": po_id,
                "reallocatable_qty": prev_realloc,
                "detail": (prior or {}).get("summary")
                or f"{po_id} reallocatable_qty={prev_realloc}; do not repeat apply.",
            }
        new_qty = prev_qty - qty
        if new_qty < 1:
            return {"error": True, "detail": f"{po_id} must keep quantity > 0 (CHECK)"}
        new_realloc = max(0, prev_realloc - qty)
        conn.execute(
            "UPDATE twin_purchase_orders SET quantity = %s, reallocatable_qty = %s WHERE po_id = %s",
            (new_qty, new_realloc, po_id),
        )
        inverse = {
            "po_id": po_id,
            "quantity": prev_qty,
            "reallocatable_qty": prev_realloc,
        }
        action_id = record_action(
            conn,
            agent_name="Disruption_HITL",
            action_type="REALLOCATE_TWIN_PO",
            payload={"po_id": po_id, "moved_qty": qty, "quantity": new_qty, "reallocatable_qty": new_realloc},
            inverse=inverse,
            transaction_id=None,
        )
    out = {
        "ok": True,
        "applied": True,
        "decision": "approve",
        "po_id": po_id,
        "moved_qty": qty,
        "quantity": new_qty,
        "reallocatable_qty": new_realloc,
        "action_id": action_id,
        "inverse": inverse,
    }
    try:
        from agents.memory.store import record_experience

        record_experience(
            flow="disruption_apply",
            summary=f"Moved {qty} off {po_id} (now qty {new_qty}, reallocatable {new_realloc}).",
            outcome={"moved_qty": qty, "quantity": new_qty, "action_id": action_id},
            po_id=po_id,
            vendor_id=(recommendation or {}).get("vendor_id"),
            material_id=(recommendation or {}).get("material_id"),
        )
    except Exception:  # noqa: BLE001
        pass
    return out


def undo_reallocation(action_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT inverse, payload FROM actions WHERE action_id = %s AND action_type = 'REALLOCATE_TWIN_PO'",
            (action_id,),
        ).fetchone()
        if not row:
            return {"error": True, "detail": f"Unknown REALLOCATE_TWIN_PO {action_id}"}
        inverse = row["inverse"] if isinstance(row, dict) else row[0]
        if isinstance(inverse, str):
            import json

            inverse = json.loads(inverse)
        po_id = inverse["po_id"]
        conn.execute(
            "UPDATE twin_purchase_orders SET quantity = %s, reallocatable_qty = %s, status = 'OPEN' WHERE po_id = %s",
            (inverse["quantity"], inverse["reallocatable_qty"], po_id),
        )
        undo_id = record_action(
            conn,
            agent_name="Disruption_HITL",
            action_type="UNDO_REALLOCATE_TWIN_PO",
            payload=inverse,
            inverse={"noop": True, "note": "already restored"},
        )
    out = {"ok": True, "undone": True, "po_id": po_id, "action_id": undo_id}
    try:
        from agents.memory.store import record_experience

        record_experience(
            flow="disruption_undo",
            summary=f"Restored {po_id} to qty {inverse['quantity']}.",
            outcome={"action_id": undo_id, "inverse": inverse},
            po_id=po_id,
        )
    except Exception:  # noqa: BLE001
        pass
    return out
