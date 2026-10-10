"""Queryable agent memory for a trace (prompts, turns, blackboard, experience)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["Memory"])


@router.get("/v1/memory/runs/{trace_id}")
def get_run_memory(trace_id: str):
    from agents.memory.store import run_memory

    return run_memory(trace_id)
