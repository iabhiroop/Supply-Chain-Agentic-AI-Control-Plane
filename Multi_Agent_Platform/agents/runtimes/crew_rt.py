"""Crew-shaped sequential specialists. Uses the same node functions as LangGraph.

The `crewai` package is optional. When installed, this still calls identical twin
tools / policy — numbers must not drift from the LangGraph golden.
"""

from __future__ import annotations

import uuid
from typing import Any

from agents.disruption.nodes.demand import demand_node
from agents.disruption.nodes.inventory import inventory_node
from agents.disruption.nodes.logistics import logistics_node
from agents.disruption.nodes.orchestrator import orchestrator_node
from agents.disruption.nodes.procurement import procurement_node
from agents.disruption.nodes.risk import risk_node
from agents.execution.trace import mint_trace_id, reset_trace_id, set_trace_id
from agents.runtimes.state_util import blank_disruption, merge_update

CREW = [
    ("risk", risk_node),
    ("inventory", inventory_node),
    ("demand", demand_node),
    ("logistics", logistics_node),
    ("procurement", procurement_node),
    ("orchestrator", orchestrator_node),
]


async def run_crew_disruption(
    *,
    vendor_id: str = "VEND-ABC",
    material_id: str = "MAT-SEMI-1",
    alternate_vendor_id: str = "VEND-XYZ",
    trace_id: str | None = None,
) -> dict[str, Any]:
    trace_id = trace_id or mint_trace_id("crew")
    token = set_trace_id(trace_id)
    try:
        state = blank_disruption(
            vendor_id=vendor_id,
            material_id=material_id,
            alternate_vendor_id=alternate_vendor_id,
        )
        config = {"configurable": {"thread_id": trace_id, "agent_name": "Crew_Runtime"}}
        for role, node in CREW:
            config["configurable"]["agent_name"] = role
            update = await node(state, config)
            merge_update(state, update)
        rec = state.get("recommendation") or {}
        return {
            "run_id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "vendor_id": vendor_id,
            "material_id": material_id,
            "alternate_vendor_id": alternate_vendor_id,
            "recommendation": rec,
            "agent_results": state.get("agent_results") or {},
            "completed_agents": state.get("completed_agents") or [],
            "tool_calls": state.get("tool_calls") or 0,
            "status": "RECOMMENDED" if rec else "FAILED",
            "control_plane": "crew-sequential",
        }
    finally:
        reset_trace_id(token)
