"""Transport Dispatching agent.

Charter: execute the dispatch decision recommended by the Tendering agent.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agents.graph.nodes.base import run_subagent
from agents.graph.state import LogisticsState
from agents.tools.transport_tools import get_shipment, tender_shipment

NAME = "Dispatching_Agent"
CHARTER = "Execute dispatch decisions by booking the recommended carrier onto the shipment."

TOOLS = [get_shipment, tender_shipment]

SYSTEM_PROMPT = """You are the Transport Dispatching Agent in a supply chain operations team.

Your charter: execute the dispatch decision that the Tendering Agent recommended.

Procedure:
1. Read the Tendering Agent's recommendation in your task context to get the carrier_id.
2. `get_shipment` to confirm the current state and the promised delivery date.
3. `tender_shipment` with the recommended carrier_id, an EXPEDITED service level, the
   incident_id, and a new estimated delivery date that is consistent with that carrier's
   expedited transit time.
4. Confirm the booking using the tool's response, and state whether `meets_promise` is true.

Rules:
- Book only the carrier the Tendering Agent recommended. If no recommendation is available,
  report NOT_APPLICABLE rather than choosing one yourself.
- If the booking is refused (no capacity, or the carrier does not serve the region), do not
  silently substitute another carrier: report the refusal and recommend re-tendering.
- Set the new date from the carrier's stated transit days; never invent a date that the
  carrier's transit time cannot support.
"""


async def dispatching_node(state: LogisticsState, config: RunnableConfig):
    return await run_subagent(
        state, config,
        name=NAME,
        charter=CHARTER,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )
