"""State for the disruption decision graph (not the P2P stage machine)."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from agents.graph.state import merge_dicts


class DisruptionState(TypedDict, total=False):
    vendor_id: str
    material_id: str
    alternate_vendor_id: str
    messages: Annotated[Sequence[BaseMessage], add_messages]
    agent_results: Annotated[dict[str, Any], merge_dicts]
    completed_agents: Annotated[list[str], operator.add]
    recommendation: dict[str, Any]
    halt_reason: str
    step_count: Annotated[int, operator.add]
    tool_calls: Annotated[int, operator.add]
    llm_calls: Annotated[int, operator.add]
    escalations: Annotated[list[str], operator.add]
    stage: str
    hitl_request_id: str
