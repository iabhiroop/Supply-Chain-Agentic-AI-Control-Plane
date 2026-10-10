"""A2A-style mailbox: specialists post messages; orchestrator reads the bus then policy."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from agents.disruption.policy import build_recommendation
from agents.execution.trace import mint_trace_id, reset_trace_id, set_trace_id
from agents.tools import twin_tools


async def run_a2a_disruption(
    *,
    vendor_id: str = "VEND-ABC",
    material_id: str = "MAT-SEMI-1",
    alternate_vendor_id: str = "VEND-XYZ",
    trace_id: str | None = None,
) -> dict[str, Any]:
    trace_id = trace_id or mint_trace_id("a2a")
    token = set_trace_id(trace_id)
    bus: list[dict[str, Any]] = []

    async def post(agent: str, kind: str, payload: Any) -> None:
        bus.append({"agent": agent, "kind": kind, "payload": payload})
        try:
            from agents.memory.store import record_message

            record_message(from_agent=agent, kind=kind, payload=payload, trace_id=trace_id)
        except Exception:  # noqa: BLE001
            pass

    try:
        news = await asyncio.to_thread(twin_tools.news_signals, vendor_id)
        await post("Supplier_Risk_Agent", "news", news)
        positions = await asyncio.to_thread(twin_tools.inventory_days_of_cover, material_id)
        await post("Inventory_Agent", "cover", positions)
        pos = await asyncio.to_thread(twin_tools.open_pos, vendor_id)
        await post("Procurement_Agent", "open_pos", pos)
        primary = await asyncio.to_thread(twin_tools.vendor_material, vendor_id, material_id)
        alternate = await asyncio.to_thread(twin_tools.vendor_material, alternate_vendor_id, material_id)
        await post("Procurement_Agent", "vendors", {"primary": primary, "alternate": alternate})

        rec = build_recommendation(
            vendor_id=vendor_id,
            material_id=material_id,
            positions=next((m["payload"] for m in bus if m["kind"] == "cover"), []),
            pos=next((m["payload"] for m in bus if m["kind"] == "open_pos"), []),
            primary=primary,
            alternate=alternate,
        )
        payload = rec.model_dump(mode="json")
        await post("Disruption_Orchestrator", "recommendation", payload)
        return {
            "run_id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "vendor_id": vendor_id,
            "material_id": material_id,
            "alternate_vendor_id": alternate_vendor_id,
            "recommendation": payload,
            "agent_results": {m["agent"]: {"status": "OK", "kind": m["kind"]} for m in bus},
            "completed_agents": [m["agent"] for m in bus],
            "tool_calls": 5,
            "status": "RECOMMENDED",
            "control_plane": "a2a-mailbox",
            "messages": len(bus),
        }
    finally:
        reset_trace_id(token)
