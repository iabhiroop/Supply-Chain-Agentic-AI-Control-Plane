"""Demand — horizon vs cover → shortage units. Does not pick a vendor."""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from agents.contracts import AgentResult, AgentStatus
from agents.disruption.policy import short_plants
from agents.disruption.state import DisruptionState
from agents.execution.loop import pack_turn
from agents.memory.store import peer_citations
from agents.tools import twin_tools

NAME = "Demand_Agent"


async def demand_node(state: DisruptionState, config: RunnableConfig):
    material_id = state.get("material_id") or "MAT-SEMI-1"
    positions = await asyncio.to_thread(twin_tools.inventory_days_of_cover, material_id)
    plants = short_plants(positions)
    total = round(sum(p.shortage_units for p in plants), 2)
    findings = [
        f"{p.plant_id}: horizon {p.horizon_days}d vs cover {p.days_of_cover:g}d "
        f"→ {p.shortage_units:g} units short"
        for p in plants
    ]
    if not findings:
        findings = [f"No short-cover plants on {material_id}."]
    result = AgentResult(
        status=AgentStatus.SUCCESS if plants else AgentStatus.PARTIAL,
        summary=f"Expected demand requires a total shortage of {total:g} units across short-cover plants.",
        findings=findings,
        evidence=["twin.inventory_positions", "twin.independent_demand"]
        + peer_citations(state, "Inventory_Agent", "Supplier_Risk_Agent"),
        confidence=1.0 if plants else 0.5,
        verified=True,
        recommendation="Logistics should identify an alternate with a qualifying lead time.",
    )
    facts = {
        "shortage_units": total,
        "horizon_short_plants": [p.model_dump(mode="json") for p in plants],
    }
    if plants:
        facts["horizon"] = max(p.horizon_days for p in plants)
    update = pack_turn(
        NAME,
        result,
        tool_calls=1,
        facts=facts,
        workflow="disruption",
    )
    update["halt_reason"] = ""
    return update
