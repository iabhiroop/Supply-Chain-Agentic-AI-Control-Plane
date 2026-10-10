"""Procurement — premium, 40% PO-1842, RFQ flag. Does not post a PO."""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from agents.contracts import AgentResult, AgentStatus
from agents.disruption.policy import REALLOCATE_PO, premium_pct, reallocate_spec
from agents.disruption.state import DisruptionState
from agents.execution.loop import pack_turn
from agents.memory.store import peer_citations
from agents.tools import twin_tools

NAME = "Procurement_Agent"


async def procurement_node(state: DisruptionState, config: RunnableConfig):
    vendor_id = state.get("vendor_id") or "VEND-ABC"
    material_id = state.get("material_id") or "MAT-SEMI-1"
    alt_id = state.get("alternate_vendor_id") or "VEND-XYZ"
    pos = await asyncio.to_thread(twin_tools.open_pos, vendor_id)
    primary = await asyncio.to_thread(twin_tools.vendor_material, vendor_id, material_id)
    alternate = await asyncio.to_thread(twin_tools.vendor_material, alt_id, material_id)
    realloc = reallocate_spec(pos)
    prem = premium_pct((primary or {}).get("unit_price"), (alternate or {}).get("unit_price"))
    findings = [
        f"{len(pos)} open POs for {vendor_id}",
        f"primary unit_price={None if not primary else primary.get('unit_price')}",
        f"alternate unit_price={None if not alternate else alternate.get('unit_price')} premium={prem:g}%",
    ]
    if realloc:
        findings.append(
            f"{realloc.po_id} reallocatable {realloc.qty:g}/{realloc.po_quantity:g} ({realloc.pct_of_po:g}%)"
        )
    else:
        findings.append(f"{REALLOCATE_PO} not on the open book")
    result = AgentResult(
        status=AgentStatus.SUCCESS if realloc and alternate else AgentStatus.PARTIAL,
        summary=(
            f"{alt_id} costs {prem:g}% more; reallocate {realloc.pct_of_po:g}% of {realloc.po_id}."
            if realloc
            else f"{alt_id} premium {prem:g}%; no reallocatable PO."
        ),
        findings=findings,
        evidence=["twin.twin_purchase_orders", "twin.vendor_materials"]
        + peer_citations(state, "Logistics_Agent"),
        actions_taken=[],
        confidence=1.0 if realloc else 0.5,
        verified=bool(realloc),
        recommendation="Orchestrator should assemble the typed recommendation (no PO post).",
    )
    facts = {"premium_pct": prem}
    if realloc:
        facts["reallocate"] = realloc.model_dump(mode="json")
    if alternate:
        facts["alternate"] = {
            "vendor_id": alt_id,
            "unit_price": alternate.get("unit_price"),
            "lead_time_days": alternate.get("lead_time_days"),
        }
    update = pack_turn(NAME, result, tool_calls=3, facts=facts, workflow="disruption")
    update["halt_reason"] = ""
    return update
