"""P2P execution graph: Sourcing, Procurement, Logistics, Finance.

Warehouse specialists remain on disk (warehouse_graph.py) but are not members
of this supervisor.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.graph.nodes.finance import finance_node
from agents.graph.nodes.logistics import logistics_node
from agents.graph.nodes.procurement import procurement_node
from agents.graph.nodes.sourcing import sourcing_node
from agents.graph.state import LogisticsState
from agents.graph.supervisor import FINISH, members, supervisor_node

NODES = {
    "Sourcing_Agent": sourcing_node,
    "Procurement_Agent": procurement_node,
    "Logistics_Agent": logistics_node,
    "Finance_Agent": finance_node,
}


def build_graph(checkpointer=None):
    workflow = StateGraph(LogisticsState)
    workflow.add_node("Supervisor", supervisor_node)
    for name, node in NODES.items():
        workflow.add_node(name, node)
    for name in members:
        workflow.add_edge(name, "Supervisor")
    workflow.add_conditional_edges(
        "Supervisor",
        lambda state: state.get("next_agent", FINISH),
        {**{name: name for name in members}, FINISH: END},
    )
    workflow.add_edge(START, "Supervisor")
    return workflow.compile(checkpointer=checkpointer or MemorySaver())


orchestrator_app = build_graph()
