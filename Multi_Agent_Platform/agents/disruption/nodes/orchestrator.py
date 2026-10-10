"""Orchestrator — assemble DisruptionRecommendation from twin facts. No provider calls."""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from agents.contracts import AgentResult, AgentStatus
from agents.disruption.policy import build_recommendation
from agents.disruption.state import DisruptionState
from agents.execution.loop import pack_turn
from agents.memory.store import get_facts, peer_citations
from agents.tools import twin_tools

NAME = "Disruption_Orchestrator"


async def orchestrator_node(state: DisruptionState, config: RunnableConfig):
    vendor_id = state.get("vendor_id") or "VEND-ABC"
    material_id = state.get("material_id") or "MAT-SEMI-1"
    alt_id = state.get("alternate_vendor_id") or "VEND-XYZ"

    unsure: list[tuple[str, AgentResult]] = []
    for name, payload in (state.get("agent_results") or {}).items():
        try:
            parsed = AgentResult(**payload) if not isinstance(payload, AgentResult) else payload
        except Exception:  # noqa: BLE001
            continue
        if parsed.needs_escalation():
            unsure.append((name, parsed))
    if unsure:
        from agents.execution.hitl import create_hitl_request

        name, parsed = unsure[0]
        parked = create_hitl_request(
            question=parsed.summary or f"{name} is not confident enough to recommend a disruption response.",
            transaction_id=None,
            workflow="disruption",
            agent_name=name,
            step="recommend",
            options=[
                {"id": "proceed", "label": "Publish the recommendation anyway"},
                {"id": "keep_waiting", "label": "Hold — gather more facts"},
                {"id": "cancel", "label": "Drop this disruption run"},
            ],
            recommended_option="keep_waiting",
            rationale="; ".join(unsure_name for unsure_name, _ in unsure),
            confidence=parsed.confidence,
            evidence=list(parsed.evidence or parsed.findings or []),
            context={
                "vendor_id": vendor_id,
                "material_id": material_id,
                "unsure_agents": [n for n, _ in unsure],
            },
            park=False,
        )
        result = AgentResult(
            status=AgentStatus.NEEDS_HUMAN,
            summary=parked.get("question") or parsed.summary,
            findings=[f"{n}: conf={r.confidence:.2f} {r.status.value}" for n, r in unsure],
            confidence=min(r.confidence for _, r in unsure),
            verified=False,
            recommendation="Human must pick an option before a recommendation is published.",
        )
        update = pack_turn(NAME, result, tool_calls=0, workflow="disruption")
        update["recommendation"] = {}
        update["halt_reason"] = result.summary
        update["hitl_request_id"] = parked.get("request_id")
        return update

    positions = await asyncio.to_thread(twin_tools.inventory_days_of_cover, material_id)
    pos = await asyncio.to_thread(twin_tools.open_pos, vendor_id)
    primary = await asyncio.to_thread(twin_tools.vendor_material, vendor_id, material_id)
    alternate = await asyncio.to_thread(twin_tools.vendor_material, alt_id, material_id)
    rec = build_recommendation(
        vendor_id=vendor_id,
        material_id=material_id,
        positions=positions,
        pos=pos,
        primary=primary,
        alternate=alternate,
    )
    payload = rec.model_dump(mode="json")
    board = get_facts()
    cited = [f"blackboard.{k}" for k in board]
    result = AgentResult(
        status=AgentStatus.SUCCESS,
        summary=rec.summary,
        findings=[
            f"short_plants={[p.plant_id for p in rec.short_plants]}",
            f"affected_pos={rec.affected_po_ids}",
            f"shortage_units={rec.shortage_units}",
            f"expedite_rfq={rec.expedite_rfq}",
        ],
        evidence=["twin SQL via policy.build_recommendation"]
        + cited
        + peer_citations(
            state,
            "Supplier_Risk_Agent",
            "Inventory_Agent",
            "Demand_Agent",
            "Logistics_Agent",
            "Procurement_Agent",
        ),
        recommendation=rec.summary,
        confidence=1.0,
        verified=True,
    )
    update = pack_turn(
        NAME,
        result,
        tool_calls=4,
        facts={"recommendation": payload},
        workflow="disruption",
    )
    update["recommendation"] = payload
    update["halt_reason"] = ""
    return update
