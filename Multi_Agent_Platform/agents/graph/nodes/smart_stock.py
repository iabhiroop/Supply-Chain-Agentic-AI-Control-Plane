"""Smart Stock Adjustment agent.

Charter: verify and correct inventory discrepancies, replenish the pick face
from other bins when possible, and report whether the order is still short.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agents.config.settings import settings
from agents.graph.nodes.base import run_subagent
from agents.graph.state import LogisticsState
from agents.tools.customer_tools import get_order_lines
from agents.tools.inventory_tools import (
    adjust_inventory,
    get_bin_stock,
    get_stock_ledger,
    transfer_stock,
)

NAME = "Smart_Stock_Agent"
CHARTER = (
    "Establish stock transparency, correct the pick-face books, replenish from "
    "other bins in the same warehouse when possible, and report if the order is still short."
)

TOOLS = [get_bin_stock, get_stock_ledger, get_order_lines, adjust_inventory, transfer_stock]

_TRANSFER_RULE = (
    "After the pick-face quantity matches the physical count, `get_order_lines` for the "
    "order. If another bin in the *same warehouse* has enough quantity to cover the "
    "remaining line demand, `transfer_stock` from that bin onto the pick-face bin. "
    "Set replenished=true and order_still_short=false when the pick face can fill the line. "
    "Set customer_notify=false when you recovered the line in-house and the promise is not "
    "otherwise at risk."
    if settings.auto_transfer
    else
    "Do not transfer stock. Set order_still_short=true if the pick face cannot fill the line."
)

SYSTEM_PROMPT = f"""You are the Smart Stock Adjustment Agent in a supply chain operations team.

Your charter: establish stock transparency, identify the root cause of inventory
discrepancies, correct the pick-face books, and recover the order from other bins
when that is possible.

Procedure:
1. `get_bin_stock` for the product to see quantity and warehouse for every bin.
2. Compare the incident bin against the physically counted quantity in the event.
3. `get_stock_ledger` to explain the gap.
4. If there is a genuine discrepancy, `adjust_inventory` to set the incident bin to the
   physically counted quantity. Pass the incident_id and a clear reason.
5. {_TRANSFER_RULE}
6. State the consequence: order_still_short true/false, replenished true/false,
   customer_notify true only if the customer promise is still at risk.

Rules:
- If the incident event is SHIPMENT_DELAYED or CARRIER_REJECTED_LOAD and there is no
  physically counted quantity to post, do not adjust stock. Return status NOT_APPLICABLE
  with a one-line explanation that this is a transport incident.
- If the incident is GOODS_RECEIPT_DISCREPANCY, return NOT_APPLICABLE — inbound receiving
  owns that work.
- Correct the stock to the *physically counted* quantity, not to a number you infer.
- Never claim an adjustment or transfer succeeded unless the tool result confirms it.
- Do not attempt carrier or customer work; other agents own those.
"""


async def smart_stock_node(state: LogisticsState, config: RunnableConfig):
    return await run_subagent(
        state, config,
        name=NAME,
        charter=CHARTER,
        system_prompt=SYSTEM_PROMPT,
        tools=TOOLS,
    )
