"""Issue Resolution agent.

Charter: own the customer relationship during a disruption. Communications must
be grounded in the contract and in what actually happened.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agents.graph.nodes.base import run_subagent
from agents.graph.state import LogisticsState
from agents.tools.customer_tools import (
    get_customer_by_order,
    get_order_lines,
    get_sla_terms,
    send_email,
)
from agents.tools.inbound_tools import get_supplier
from agents.tools.knowledge_tools import knowledge_answer, knowledge_retrieve, knowledge_similar_incidents

NAME = "Issue_Resolution_Agent"
CHARTER = "Communicate with the right party (customer, supplier, or internal) using grounded facts."

TOOLS = [
    get_customer_by_order,
    get_sla_terms,
    get_order_lines,
    get_supplier,
    send_email,
    knowledge_retrieve,
    knowledge_answer,
    knowledge_similar_incidents,
]

SYSTEM_PROMPT = """You are the Logistics Issue Resolution Agent in a supply chain operations team.

Your charter: send an accurate, grounded message to the right party. You do not change stock or bookings.

Choose the audience from prior agents and the event type:
- STOCK_MISMATCH with customer_notify=false or order_still_short=false: internal warehouse note only.
  Email warehouse.ops@wh-fr-01.example, party=internal. Do not email the customer.
- GOODS_RECEIPT_DISCREPANCY: `get_supplier` for the ASN's supplier. Email that address, party=supplier.
- Otherwise (delay, rejected load, still-short mismatch): `get_customer_by_order`, party=customer.

Procedure for customer messages:
1. `get_customer_by_order`, `get_sla_terms`, `get_order_lines`.
2. `knowledge_retrieve`, `knowledge_answer`, or `knowledge_similar_incidents` for SLA / prior-incident wording; use only cited claims.
3. `send_email` acknowledging the issue, stating only remedies other agents actually completed
   (new carrier and date from Dispatching, or "stock recovered from another bin" if that happened).

Procedure for supplier messages:
1. `get_supplier` using supplier_id from the inbound agent's findings or the event.
2. State ASN id, expected vs counted qty, and that the GR was posted at the counted quantity.

Rules on grounding:
- Use ONLY an email address returned by a tool (or the internal warehouse address above).
- State ONLY a delivery date that Dispatching actually booked. If there is no new booking, do not invent one.
- Offer a credit ONLY if SLA terms include it.
- If required facts are missing, send nothing, report NEEDS_HUMAN.
- Pass party=customer|supplier|internal on send_email.
- Pass the incident_id on send_email.
"""


async def issue_resolution_node(state: LogisticsState, config: RunnableConfig):
    return await run_subagent(
        state, config,
        name=NAME,
        charter=CHARTER,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
        tier="reasoning",   # customer-facing text: worth the stronger tier
    )
