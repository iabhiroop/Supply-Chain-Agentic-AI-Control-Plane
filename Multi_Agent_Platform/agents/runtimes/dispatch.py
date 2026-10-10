"""Dispatch `run(case_id, runtime=...)` — identical goldens, interchangeable control planes."""

from __future__ import annotations

from typing import Any

RUNTIMES = ("langgraph", "crewai", "mcp", "a2a")
DISRUPTION_CASE = "abc_shutdown"
P2P_CASES = {
    "pr-1001": "aaaaaaaa-bbbb-cccc-dddd-000000000001",
    "pr-1002": "aaaaaaaa-bbbb-cccc-dddd-000000000002",
    "pr-1003": "aaaaaaaa-bbbb-cccc-dddd-000000000003",
}


async def run(case_id: str, runtime: str = "langgraph", **kwargs: Any) -> dict[str, Any]:
    runtime = (runtime or "langgraph").strip().lower()
    if runtime not in RUNTIMES:
        raise ValueError(f"Unknown runtime {runtime}. Choose from {RUNTIMES}")
    case = (case_id or DISRUPTION_CASE).strip().lower().replace("_", "-")
    if case in {"abc-shutdown", "abc", "taiwan", "disruption"}:
        return await _disruption(runtime, **kwargs)
    tx = P2P_CASES.get(case)
    if tx:
        if runtime != "langgraph":
            return {
                "case_id": case,
                "runtime": runtime,
                "status": "SKIPPED",
                "detail": "P2P goldens stay on the LangGraph/stage-machine spine (do not rewrite PR-1001).",
            }
        from agents.execution.runtime import execute_transaction

        result = await execute_transaction(tx, **kwargs)
        result["case_id"] = case
        result["runtime"] = runtime
        return result
    raise KeyError(f"Unknown case {case_id}")


async def _disruption(runtime: str, **kwargs: Any) -> dict[str, Any]:
    vendor_id = kwargs.get("vendor_id", "VEND-ABC")
    material_id = kwargs.get("material_id", "MAT-SEMI-1")
    alternate_vendor_id = kwargs.get("alternate_vendor_id", "VEND-XYZ")
    trace_id = kwargs.get("trace_id")
    if runtime == "langgraph":
        from agents.disruption.runtime import execute_disruption

        result = await execute_disruption(
            vendor_id=vendor_id,
            material_id=material_id,
            alternate_vendor_id=alternate_vendor_id,
            trace_id=trace_id,
        )
    elif runtime == "crewai":
        from agents.runtimes.crew_rt import run_crew_disruption

        result = await run_crew_disruption(
            vendor_id=vendor_id,
            material_id=material_id,
            alternate_vendor_id=alternate_vendor_id,
            trace_id=trace_id,
        )
    elif runtime == "mcp":
        from agents.runtimes.mcp_rt import run_mcp_disruption

        result = await run_mcp_disruption(
            vendor_id=vendor_id,
            material_id=material_id,
            alternate_vendor_id=alternate_vendor_id,
            trace_id=trace_id,
        )
    else:
        from agents.runtimes.a2a_rt import run_a2a_disruption

        result = await run_a2a_disruption(
            vendor_id=vendor_id,
            material_id=material_id,
            alternate_vendor_id=alternate_vendor_id,
            trace_id=trace_id,
        )
    result["case_id"] = "abc_shutdown"
    result["runtime"] = runtime
    return result
