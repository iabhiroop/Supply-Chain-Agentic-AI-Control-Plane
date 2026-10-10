"""MCP-style tool loop: orchestrator only sees JSON tool results, then Python policy."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from agents.disruption.policy import build_recommendation
from agents.execution.trace import mint_trace_id, reset_trace_id, set_trace_id
from agents.runtimes.mcp_tools import call_tool


async def run_mcp_disruption(
    *,
    vendor_id: str = "VEND-ABC",
    material_id: str = "MAT-SEMI-1",
    alternate_vendor_id: str = "VEND-XYZ",
    trace_id: str | None = None,
) -> dict[str, Any]:
    trace_id = trace_id or mint_trace_id("mcp")
    token = set_trace_id(trace_id)
    try:
        tools_used = [
            "news.search",
            "graph.query",
            "inventory.days_of_cover",
            "po.open_for_vendor",
            "vendor.material",
        ]
        news = await asyncio.to_thread(call_tool, "news.search", vendor_id)
        kg = await asyncio.to_thread(
            call_tool, "graph.query", f"Which plants if {vendor_id} shuts down?"
        )
        positions = await asyncio.to_thread(call_tool, "inventory.days_of_cover", material_id)
        pos = await asyncio.to_thread(call_tool, "po.open_for_vendor", vendor_id)
        primary = await asyncio.to_thread(call_tool, "vendor.material", vendor_id, material_id)
        alternate = await asyncio.to_thread(call_tool, "vendor.material", alternate_vendor_id, material_id)
        rec = build_recommendation(
            vendor_id=vendor_id,
            material_id=material_id,
            positions=positions,
            pos=pos,
            primary=primary,
            alternate=alternate,
        )
        payload = rec.model_dump(mode="json")
        try:
            from agents.contracts import AgentResult, AgentStatus
            from agents.memory.store import record_turn

            record_turn(
                agent_name="MCP_Risk",
                result=AgentResult(
                    status=AgentStatus.SUCCESS,
                    summary=f"news={len(news or [])}",
                    findings=[str(kg)[:200]],
                ),
                tool_calls=2,
                workflow="disruption",
                facts={"news.count": len(news or [])},
                trace_id=trace_id,
            )
            record_turn(
                agent_name="MCP_Orchestrator",
                result=AgentResult(
                    status=AgentStatus.SUCCESS,
                    summary=rec.summary,
                ),
                tool_calls=4,
                workflow="disruption",
                facts={"recommendation": payload},
                trace_id=trace_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return {
            "run_id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "vendor_id": vendor_id,
            "material_id": material_id,
            "alternate_vendor_id": alternate_vendor_id,
            "recommendation": payload,
            "agent_results": {
                "MCP_Risk": {"status": "OK", "summary": f"news={len(news or [])}", "findings": [str(kg)[:200]]},
                "MCP_Orchestrator": {"status": "OK", "summary": rec.summary},
            },
            "completed_agents": ["MCP_Risk", "MCP_Orchestrator"],
            "tool_calls": 6,
            "status": "RECOMMENDED",
            "control_plane": "mcp",
            "mcp_tools": tools_used,
        }
    finally:
        reset_trace_id(token)
