"""Inventory — days of cover from the twin. Does not compute shortage."""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from agents.contracts import AgentResult, AgentStatus
from agents.disruption.policy import SHORT_COVER_DAYS, as_float
from agents.disruption.state import DisruptionState
from agents.execution.loop import pack_turn
from agents.memory.store import peer_citations
from agents.tools import twin_tools

NAME = "Inventory_Agent"


async def inventory_node(state: DisruptionState, config: RunnableConfig):
    material_id = state.get("material_id") or "MAT-SEMI-1"
    positions = await asyncio.to_thread(twin_tools.inventory_days_of_cover, material_id)
    findings = []
    short = []
    for row in positions:
        cover = row.get("days_of_cover")
        findings.append(
            f"{row.get('plant_id')} on-hand {row.get('on_hand')} days_of_cover={cover}"
        )
        if cover is not None and as_float(cover) < SHORT_COVER_DAYS:
            short.append(row["plant_id"])
    result = AgentResult(
        status=AgentStatus.SUCCESS if positions else AgentStatus.FAILED,
        summary=(
            f"{len(short)} plant(s) below {SHORT_COVER_DAYS:g} days of cover: {', '.join(short)}."
            if short
            else f"No plant below {SHORT_COVER_DAYS:g} days of cover on {material_id}."
        ),
        findings=findings,
        evidence=["twin.inventory_positions"] + peer_citations(state, "Supplier_Risk_Agent"),
        confidence=1.0 if positions else 0.2,
        verified=bool(positions),
        recommendation="Demand should compare horizon_days to cover.",
    )
    evidence = ["twin.inventory_positions"] + peer_citations(state, "Supplier_Risk_Agent")
    result.evidence = evidence
    update = pack_turn(
        NAME,
        result,
        tool_calls=1,
        facts={"short_plants": short},
        workflow="disruption",
    )
    update["halt_reason"] = ""
    return update
