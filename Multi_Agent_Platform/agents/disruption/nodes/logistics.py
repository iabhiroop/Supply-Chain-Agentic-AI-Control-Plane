"""Logistics — alternate lead time. Does not change price."""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from agents.contracts import AgentResult, AgentStatus
from agents.disruption.state import DisruptionState
from agents.execution.loop import pack_turn
from agents.memory.store import peer_citations
from agents.tools import twin_tools

NAME = "Logistics_Agent"


async def logistics_node(state: DisruptionState, config: RunnableConfig):
    material_id = state.get("material_id") or "MAT-SEMI-1"
    alt_id = state.get("alternate_vendor_id") or "VEND-XYZ"
    row = await asyncio.to_thread(twin_tools.vendor_material, alt_id, material_id)
    if not row:
        result = AgentResult(
            status=AgentStatus.FAILED,
            summary=f"No vendor_materials row for {alt_id} / {material_id}.",
            findings=[],
            confidence=0.2,
            verified=False,
        )
        update = pack_turn(NAME, result, tool_calls=1, workflow="disruption")
        update["halt_reason"] = ""
        return update
    lead = int(row.get("lead_time_days") or 0)
    alt = bool(row.get("is_alternate"))
    findings = [
        f"{alt_id} lead_time_days={lead}",
        f"is_alternate={alt}",
        f"country={row.get('country')}",
    ]
    result = AgentResult(
        status=AgentStatus.SUCCESS if alt and lead else AgentStatus.PARTIAL,
        summary=f"Alternate {alt_id} can deliver in {lead} days (ALTERNATE_FOR {material_id}).",
        findings=findings,
        evidence=["twin.vendor_materials"] + peer_citations(state, "Demand_Agent"),
        confidence=1.0 if alt else 0.5,
        verified=alt,
        recommendation="Procurement should price the alternate against the primary.",
    )
    update = pack_turn(
        NAME,
        result,
        tool_calls=1,
        facts={"alternate.lead_days": lead, "alternate.vendor_id": alt_id},
        workflow="disruption",
    )
    update["halt_reason"] = ""
    return update
