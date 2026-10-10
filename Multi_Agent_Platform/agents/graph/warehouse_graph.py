"""Frozen warehouse incident graph (10248 empty-bin DAG).

Not the product spine. The live orchestrator is `agents.graph.graph`
(Sourcing / Procurement / Logistics / Finance). Incident ingest at
`POST /v1/events` still uses this module via `agents.runtime`.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agents.graph.nodes.dispatching import dispatching_node
from agents.graph.nodes.inbound_receiving import inbound_receiving_node
from agents.graph.nodes.issue_resolution import issue_resolution_node
from agents.graph.nodes.smart_stock import smart_stock_node
from agents.graph.nodes.tendering import tendering_node
from agents.graph.state import LogisticsState
from agents.graph.warehouse_supervisor import FINISH, members, supervisor_node

NODES = {
    "Smart_Stock_Agent": smart_stock_node,
    "Inbound_Receiving_Agent": inbound_receiving_node,
    "Tendering_Agent": tendering_node,
    "Dispatching_Agent": dispatching_node,
    "Issue_Resolution_Agent": issue_resolution_node,
}


def build_graph(checkpointer=None):
    """Compile the incident workflow.

    `checkpointer` is accepted so M3 can add durable checkpointing and
    interrupt/resume for approval gates without reshaping the graph.
    """
    workflow = StateGraph(LogisticsState)

    workflow.add_node("Supervisor", supervisor_node)
    for name, node in NODES.items():
        workflow.add_node(name, node)

    # Control always returns to the supervisor after a specialist acts.
    for name in members:
        workflow.add_edge(name, "Supervisor")

    workflow.add_conditional_edges(
        "Supervisor",
        lambda state: state.get("next_agent", FINISH),
        {**{name: name for name in members}, FINISH: END},
    )
    workflow.add_edge(START, "Supervisor")

    return workflow.compile(checkpointer=checkpointer)


orchestrator_app = build_graph()
