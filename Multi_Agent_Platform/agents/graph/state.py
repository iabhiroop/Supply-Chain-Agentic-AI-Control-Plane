"""Workflow state for the logistics incident graph."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def merge_dicts(left: dict[str, Any] | None, right: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer that accumulates per-agent results as the workflow progresses."""
    return {**(left or {}), **(right or {})}


class LogisticsState(TypedDict, total=False):
    """State carried through the incident workflow.

    Beyond the message history this holds the incident identity and the
    structured results of each sub-agent, so the orchestrator can arbitrate on
    data rather than by re-reading prose (plan.md §3.1, §3.3).
    """

    messages: Annotated[Sequence[BaseMessage], add_messages]

    # --- incident identity (frozen warehouse path) ---
    incident_id: str
    event: dict[str, Any]

    # --- execution transaction spine ---
    transaction_id: str
    stage: str
    tx_context: dict[str, Any]

    # --- routing ---
    next_agent: str
    routing_reason: str

    # --- structured hand-offs: agent name -> AgentResult dict ---
    agent_results: Annotated[dict[str, Any], merge_dicts]
    completed_agents: Annotated[list[str], operator.add]

    # --- bounded autonomy accounting (plan.md §3.3) ---
    step_count: Annotated[int, operator.add]
    llm_calls: Annotated[int, operator.add]
    tool_calls: Annotated[int, operator.add]

    # --- escalation / termination ---
    escalations: Annotated[list[str], operator.add]
    halt_reason: str
