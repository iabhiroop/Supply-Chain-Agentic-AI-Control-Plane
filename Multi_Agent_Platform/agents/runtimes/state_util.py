"""Merge specialist updates when not running inside LangGraph reducers."""

from __future__ import annotations

from typing import Any


def blank_disruption(
    *,
    vendor_id: str = "VEND-ABC",
    material_id: str = "MAT-SEMI-1",
    alternate_vendor_id: str = "VEND-XYZ",
) -> dict[str, Any]:
    return {
        "vendor_id": vendor_id,
        "material_id": material_id,
        "alternate_vendor_id": alternate_vendor_id,
        "agent_results": {},
        "completed_agents": [],
        "recommendation": {},
        "halt_reason": "",
        "step_count": 0,
        "tool_calls": 0,
        "llm_calls": 0,
        "escalations": [],
    }


def merge_update(state: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
    if not update:
        return state
    for key, value in update.items():
        if key in {"completed_agents", "escalations"} and isinstance(value, list):
            state[key] = list(state.get(key) or []) + value
        elif key in {"step_count", "tool_calls", "llm_calls"} and isinstance(value, (int, float)):
            state[key] = int(state.get(key) or 0) + int(value)
        elif key == "agent_results" and isinstance(value, dict):
            merged = dict(state.get(key) or {})
            merged.update(value)
            state[key] = merged
        else:
            state[key] = value
    return state
