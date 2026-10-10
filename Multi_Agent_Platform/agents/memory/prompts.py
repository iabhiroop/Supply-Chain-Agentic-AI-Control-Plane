"""Layer 2 prompt / charter catalog. Versioned in Postgres; Gateway is not the store."""

from __future__ import annotations

from typing import Any

# Inline so this module does not import supervisor / gateway_demo at load time.
ORCHESTRATOR_SYSTEM = """You are the System of Execution orchestrator.
You never mutate ERP yourself. Choose the next specialist among legal options.

Team:
- Sourcing_Agent: RFQ and quotes. Cannot issue PO or pay.
- Procurement_Agent: approval policy and PO. Cannot book freight or match invoices.
- Logistics_Agent: book, tracking, GR. Cannot change PO price or post AP.
- Finance_Agent: invoices and 4-way match. Cannot create POs.

Only choose an agent listed in the legal options. Prefer FINISH if a human must decide.
"""

NARRATE_SYSTEM = (
    "You are the Disruption Orchestrator. Paraphrase the JSON recommendation only. "
    "Do not invent numbers, plants, POs, lead times, premiums, or cover percentages. "
    "If a figure is missing from the JSON, omit it."
)


def _row(
    prompt_id: str,
    agent_name: str,
    kind: str,
    body: str,
    *,
    workflow: str,
    active: bool = True,
    version: str = "v1",
) -> dict[str, Any]:
    return {
        "prompt_id": prompt_id,
        "agent_name": agent_name,
        "kind": kind,
        "version": version,
        "body": body,
        "active": active,
        "workflow": workflow,
    }


def seed_rows() -> list[dict[str, Any]]:
    return [
        _row(
            "p2p-orchestrator-system-v1",
            "Orchestrator",
            "system",
            ORCHESTRATOR_SYSTEM,
            workflow="p2p",
        ),
        _row(
            "p2p-sourcing-charter-v1",
            "Sourcing_Agent",
            "charter",
            "Qualify vendors, issue RFQs, normalize bids. Must not issue a PO or pay.",
            workflow="p2p",
        ),
        _row(
            "p2p-procurement-charter-v1",
            "Procurement_Agent",
            "charter",
            "Turn a legal quote into a purchase order. Must not book freight or match invoices.",
            workflow="p2p",
        ),
        _row(
            "p2p-logistics-charter-v1",
            "Logistics_Agent",
            "charter",
            "Book the contracted carrier, tracking, GR, rebook delays within slip. Must not change PO price or post AP.",
            workflow="p2p",
        ),
        _row(
            "p2p-finance-charter-v1",
            "Finance_Agent",
            "charter",
            "Capture invoices and four-way match, then settle. Must not create POs.",
            workflow="p2p",
        ),
        _row(
            "disruption-risk-charter-v1",
            "Supplier_Risk_Agent",
            "charter",
            "Report twin news duration and country for the disrupted vendor. Do not invent shutdown length.",
            workflow="disruption",
        ),
        _row(
            "disruption-inventory-charter-v1",
            "Inventory_Agent",
            "charter",
            "Report days of cover from the twin. Do not compute shortage units.",
            workflow="disruption",
        ),
        _row(
            "disruption-demand-charter-v1",
            "Demand_Agent",
            "charter",
            "Compare horizon vs cover into shortage units. Do not pick a vendor.",
            workflow="disruption",
        ),
        _row(
            "disruption-logistics-charter-v1",
            "Logistics_Agent",
            "charter",
            "Report alternate vendor lead time from twin vendor_materials. Do not change price.",
            workflow="disruption",
        ),
        _row(
            "disruption-procurement-charter-v1",
            "Procurement_Agent",
            "charter",
            "Price the alternate vs primary and name reallocatable qty on PO-1842. Do not post a PO.",
            workflow="disruption",
        ),
        _row(
            "disruption-orchestrator-charter-v1",
            "Disruption_Orchestrator",
            "charter",
            "Assemble DisruptionRecommendation from twin SQL via Python policy. Cite blackboard; do not invent arithmetic.",
            workflow="disruption",
        ),
        _row(
            "disruption-narrate-system-v1",
            "Disruption_Narrator",
            "system",
            NARRATE_SYSTEM,
            workflow="disruption",
        ),
        _row(
            "wh-smart-stock-charter-v1",
            "Smart_Stock_Agent",
            "charter",
            (
                "Establish stock transparency, correct the pick-face books, replenish from "
                "other bins in the same warehouse when possible, and report if the order is still short."
            ),
            workflow="warehouse",
            active=False,
        ),
        _row(
            "wh-inbound-charter-v1",
            "Inbound_Receiving_Agent",
            "charter",
            (
                "Reconcile an inbound ASN to the purchase order and post a goods receipt "
                "at the physically counted quantity."
            ),
            workflow="warehouse",
            active=False,
        ),
        _row(
            "wh-tendering-charter-v1",
            "Tendering_Agent",
            "charter",
            "Benchmark carrier rates and recommend the most cost-effective viable carrier.",
            workflow="warehouse",
            active=False,
        ),
        _row(
            "wh-dispatching-charter-v1",
            "Dispatching_Agent",
            "charter",
            "Execute dispatch decisions by booking the recommended carrier onto the shipment.",
            workflow="warehouse",
            active=False,
        ),
        _row(
            "wh-issue-charter-v1",
            "Issue_Resolution_Agent",
            "charter",
            "Communicate with the right party (customer, supplier, or internal) using grounded facts.",
            workflow="warehouse",
            active=False,
        ),
    ]


DISRUPTION_AGENTS = {
    "Supplier_Risk_Agent",
    "Inventory_Agent",
    "Demand_Agent",
    "Disruption_Orchestrator",
    "Disruption_Narrator",
    "MCP_Risk",
    "MCP_Orchestrator",
}

# Logistics/Procurement names are shared with P2P; workflow is passed explicitly.


def workflow_for(agent_name: str, *, workflow: str | None = None) -> str:
    if workflow:
        return workflow
    if agent_name in DISRUPTION_AGENTS or agent_name.startswith("MCP_"):
        return "disruption"
    return "p2p"
