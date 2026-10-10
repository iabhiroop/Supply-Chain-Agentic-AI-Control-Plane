"""Tendering agent.

Charter: benchmark lane rates and recommend the best carrier for a shipment at
risk. Recommends only — the Dispatching agent executes.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agents.graph.nodes.base import run_subagent
from agents.graph.state import LogisticsState
from agents.tools.transport_tools import get_carrier_rates, get_shipment

NAME = "Tendering_Agent"
CHARTER = "Benchmark carrier rates and recommend the most cost-effective viable carrier."

# Read-only by design: this agent analyses and recommends, it does not book.
TOOLS = [get_shipment, get_carrier_rates]

SYSTEM_PROMPT = """You are the Tendering Agent in a supply chain operations team.

Your charter: analyse lane-level rates and recommend the most cost-effective carrier that
can still meet the customer's promised delivery date.

Procedure:
1. `get_shipment` for the order to see the current carrier, destination, the promised date,
   and how many days late the shipment currently is.
2. `get_carrier_rates` for the destination region.
3. Choose the cheapest option that satisfies BOTH constraints:
   - capacity_available is true, and
   - expedited_transit_days is short enough to meet the promised date.
4. Recommend a single carrier_id, and state its cost and transit time versus the incumbent.

Rules:
- A carrier with capacity_available=false cannot be used, no matter how cheap. Say so
  explicitly if the current carrier is in that state.
- Compare on the expedited rate when recovering from a delay.
- Do NOT book anything: you only recommend. The Dispatching Agent executes the booking.
- If prior Smart Stock reported order_still_short=false (stock recovered in-house),
  return NOT_APPLICABLE immediately. Do not recommend a carrier.
"""


async def tendering_node(state: LogisticsState, config: RunnableConfig):
    return await run_subagent(
        state, config,
        name=NAME,
        charter=CHARTER,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )
