"""Disruption recommendation control plane. Default run is recommend-only; apply is HITL."""

from __future__ import annotations

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from agents.disruption.runtime import execute_disruption
from agents.disruption.gateway_demo import taiwan_chat

router = APIRouter(tags=["Disruption"])


class DisruptionRunBody(BaseModel):
    vendor_id: str = Field(default="VEND-ABC")
    material_id: str = Field(default="MAT-SEMI-1")
    alternate_vendor_id: str = Field(default="VEND-XYZ")
    runtime: str = Field(default="langgraph")


class NarrateBody(BaseModel):
    recommendation: dict
    trace_id: str
    agent_id: str = "Procurement_Agent"


class ApplyBody(BaseModel):
    recommendation: dict
    decision: str = "approve"


class UndoBody(BaseModel):
    action_id: str


@router.post("/v1/disruption/run")
async def run_disruption(
    body: DisruptionRunBody | None = None,
    x_trace_id: str | None = Header(default=None),
):
    payload = body or DisruptionRunBody()
    if (payload.runtime or "langgraph") != "langgraph":
        from agents.runtimes.dispatch import run as run_case

        return await run_case(
            "abc_shutdown",
            runtime=payload.runtime,
            vendor_id=payload.vendor_id,
            material_id=payload.material_id,
            alternate_vendor_id=payload.alternate_vendor_id,
            trace_id=x_trace_id,
        )
    return await execute_disruption(
        vendor_id=payload.vendor_id,
        material_id=payload.material_id,
        alternate_vendor_id=payload.alternate_vendor_id,
        trace_id=x_trace_id,
    )


@router.post("/v1/disruption/narrate")
async def narrate_disruption(body: NarrateBody):
    """Grounded paraphrase through the Gateway. Numbers stay in the JSON."""
    return taiwan_chat(
        recommendation=body.recommendation,
        trace_id=body.trace_id,
        agent_id=body.agent_id,
    )


@router.post("/v1/disruption/apply")
async def apply_disruption(body: ApplyBody):
    """HITL mutate of PO-1842. Default run path still recommend-only."""
    from agents.disruption.apply import apply_reallocation

    return apply_reallocation(body.recommendation, decision=body.decision)


@router.post("/v1/disruption/undo")
async def undo_disruption(body: UndoBody):
    from agents.disruption.apply import undo_reallocation

    return undo_reallocation(body.action_id)
