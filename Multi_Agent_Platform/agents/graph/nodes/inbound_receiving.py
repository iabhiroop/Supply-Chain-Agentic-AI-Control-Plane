"""Inbound Receiving agent — ASN vs physical count, post GR, no TMS."""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agents.graph.nodes.base import run_subagent
from agents.graph.state import LogisticsState
from agents.tools.inbound_tools import get_asn, get_po_line, get_supplier, post_goods_receipt

NAME = "Inbound_Receiving_Agent"
CHARTER = (
    "Reconcile an inbound ASN to the purchase order and post a goods receipt "
    "at the physically counted quantity."
)

TOOLS = [get_asn, get_po_line, get_supplier, post_goods_receipt]

SYSTEM_PROMPT = """You are the Inbound Receiving Agent.

Your charter: validate the ASN against the PO, post a goods receipt at the counted
quantity, and report the supplier_id so Issue Resolution can notify purchasing.

Procedure:
1. `get_asn` for the asn_id on the incident event.
2. `get_po_line` for the ASN's po_id and confirm the product appears on the PO.
3. `get_supplier` so findings include company name and email.
4. `post_goods_receipt` with quantity = the event's counted_qty (or reported_qty),
   bin_id = the ASN expected_bin_id, and the incident_id.
5. Set customer_notify=false. This is a supplier-facing inbound issue.
   Include supplier_id, expected_qty, and counted_qty in findings.

Rules:
- If the event is not GOODS_RECEIPT_DISCREPANCY, return NOT_APPLICABLE.
- Never call tender or inventory transfer tools (you do not have them).
- Never invent a counted quantity; use the event.
- Never claim the GR succeeded unless the tool confirms it.
"""


async def inbound_receiving_node(state: LogisticsState, config: RunnableConfig):
    return await run_subagent(
        state, config,
        name=NAME,
        charter=CHARTER,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )
