"""LangGraph disruption pipeline. Separate from the P2P orchestrator_app."""

from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.disruption.nodes.demand import demand_node
from agents.disruption.nodes.inventory import inventory_node
from agents.disruption.nodes.logistics import logistics_node
from agents.disruption.nodes.orchestrator import orchestrator_node
from agents.disruption.nodes.procurement import procurement_node
from agents.disruption.nodes.risk import risk_node
from agents.disruption.state import DisruptionState


def build_graph(checkpointer=None):
    workflow = StateGraph(DisruptionState)
    workflow.add_node("Supplier_Risk_Agent", risk_node)
    workflow.add_node("Inventory_Agent", inventory_node)
    workflow.add_node("Demand_Agent", demand_node)
    workflow.add_node("Logistics_Agent", logistics_node)
    workflow.add_node("Procurement_Agent", procurement_node)
    workflow.add_node("Disruption_Orchestrator", orchestrator_node)
    workflow.add_edge(START, "Supplier_Risk_Agent")
    workflow.add_edge("Supplier_Risk_Agent", "Inventory_Agent")
    workflow.add_edge("Inventory_Agent", "Demand_Agent")
    workflow.add_edge("Demand_Agent", "Logistics_Agent")
    workflow.add_edge("Logistics_Agent", "Procurement_Agent")
    workflow.add_edge("Procurement_Agent", "Disruption_Orchestrator")
    workflow.add_edge("Disruption_Orchestrator", END)
    return workflow.compile(checkpointer=checkpointer or MemorySaver())


disruption_app = build_graph()
