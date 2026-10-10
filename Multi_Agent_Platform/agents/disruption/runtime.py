"""Run the disruption decision spine. Recommend only — never mutates twin POs."""

from __future__ import annotations

import uuid
from typing import Any

from agents.config.db import get_conn
from agents.contracts import DisruptionRecommendation
from agents.disruption.graph import disruption_app
from agents.execution.trace import mint_trace_id, reset_trace_id, set_trace_id
from api.database.db import record_action


async def execute_disruption(
    *,
    vendor_id: str = "VEND-ABC",
    material_id: str = "MAT-SEMI-1",
    alternate_vendor_id: str = "VEND-XYZ",
    trace_id: str | None = None,
) -> dict[str, Any]:
    from agents.memory.store import ensure

    ensure()
    trace_id = trace_id or mint_trace_id("disruption")
    token = set_trace_id(trace_id)
    try:
        state = await disruption_app.ainvoke(
            {
                "vendor_id": vendor_id,
                "material_id": material_id,
                "alternate_vendor_id": alternate_vendor_id,
                "agent_results": {},
                "completed_agents": [],
                "recommendation": {},
                "halt_reason": "",
            },
            config={"configurable": {"thread_id": trace_id, "trace_id": trace_id}},
        )
        rec_raw = state.get("recommendation") or {}
        rec = DisruptionRecommendation(**rec_raw) if rec_raw else None
        hitl_request_id = state.get("hitl_request_id")
        halt_reason = state.get("halt_reason") or ""
        if not rec and not hitl_request_id:
            for payload in (state.get("agent_results") or {}).values():
                if isinstance(payload, dict) and payload.get("status") == "NEEDS_HUMAN":
                    halt_reason = halt_reason or payload.get("summary") or ""
                    break
        run_id = str(uuid.uuid4())
        action_id = None
        record_error = None
        if rec:
            try:
                with get_conn() as conn:
                    action_id = record_action(
                        conn,
                        agent_name="Disruption_Orchestrator",
                        action_type="RECOMMEND_DISRUPTION",
                        payload=rec.model_dump(mode="json"),
                        inverse={"noop": True, "note": "recommendation only; no PO mutation"},
                        transaction_id=None,
                    )
            except Exception as exc:  # noqa: BLE001
                record_error = str(exc)
        result = {
            "run_id": run_id,
            "trace_id": trace_id,
            "vendor_id": vendor_id,
            "material_id": material_id,
            "alternate_vendor_id": alternate_vendor_id,
            "recommendation": rec.model_dump(mode="json") if rec else rec_raw,
            "agent_results": state.get("agent_results") or {},
            "completed_agents": state.get("completed_agents") or [],
            "tool_calls": state.get("tool_calls") or 0,
            "action_id": action_id,
            "record_error": record_error,
            "halt_reason": halt_reason,
            "hitl_request_id": hitl_request_id,
            "status": "AWAITING_HUMAN" if hitl_request_id else ("RECOMMENDED" if rec else "FAILED"),
        }
        try:
            from agents.execution.eval_emit import emit_disruption_trace

            emit_disruption_trace(result)
        except Exception:  # noqa: BLE001
            pass
        try:
            from agents.memory.store import record_experience

            if rec:
                rec_json = result.get("recommendation") or {}
                realloc = rec_json.get("reallocate") or {}
                prompt_ids = [
                    p.get("_prompt_id")
                    for p in (result.get("agent_results") or {}).values()
                    if isinstance(p, dict) and p.get("_prompt_id")
                ]
                record_experience(
                    flow="disruption_recommend",
                    summary=rec_json.get("summary") or result.get("status") or "disruption",
                    outcome={
                        "status": result.get("status"),
                        "alternate": (rec_json.get("alternate") or {}).get("vendor_id"),
                        "reallocate": realloc,
                    },
                    vendor_id=vendor_id,
                    material_id=material_id,
                    po_id=realloc.get("po_id"),
                    trace_id=trace_id,
                    prompt_ids=prompt_ids,
                )
        except Exception:  # noqa: BLE001
            pass
        return result
    finally:
        reset_trace_id(token)
