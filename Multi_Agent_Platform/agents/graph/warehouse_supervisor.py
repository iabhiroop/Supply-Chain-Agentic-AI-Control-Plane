"""Frozen warehouse supervisor (10248 incident DAG). Not used by the P2P spine."""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from agents.config.settings import get_llm, settings
from agents.contracts import AgentResult, AgentStatus
from agents.graph.state import LogisticsState

OUTBOUND_MEMBERS = [
    "Smart_Stock_Agent",
    "Tendering_Agent",
    "Dispatching_Agent",
    "Issue_Resolution_Agent",
]
INBOUND_AGENT = "Inbound_Receiving_Agent"
members = OUTBOUND_MEMBERS + [INBOUND_AGENT]
options = ["FINISH"] + members

FINISH = "FINISH"

TRANSPORT_ONLY = {"SHIPMENT_DELAYED", "CARRIER_REJECTED_LOAD"}
INBOUND_EVENT = "GOODS_RECEIPT_DISCREPANCY"


class RouteResponse(BaseModel):
    next_agent: Literal[
        "FINISH",
        "Smart_Stock_Agent",
        "Tendering_Agent",
        "Dispatching_Agent",
        "Issue_Resolution_Agent",
        "Inbound_Receiving_Agent",
    ] = Field(description="Which specialist acts next, or FINISH when the incident is resolved.")
    reason: str = Field(default="", description="One sentence explaining the choice.")


SYSTEM_PROMPT = """You are the Logistics Orchestration Agent, supervising a team of specialists.
You never do the domain work yourself; you decide who acts next.

Your team:
1. Smart_Stock_Agent — verifies inventory, replenishes the pick face, reports if still short.
2. Inbound_Receiving_Agent — posts goods receipt vs ASN for inbound discrepancies.
3. Tendering_Agent — recommends a carrier. Cannot book.
4. Dispatching_Agent — books the carrier Tendering recommended.
5. Issue_Resolution_Agent — customer, supplier, or internal communication.

Known DAGs (prefer these; do not invent extra agents):
- STOCK_MISMATCH, still short: Stock -> Tendering -> Dispatching -> Issue -> FINISH
- STOCK_MISMATCH, recovered in-house: Stock -> Issue (internal) -> FINISH. Skip TMS.
- SHIPMENT_DELAYED / CARRIER_REJECTED_LOAD: Tendering -> Dispatching -> Issue -> FINISH
- GOODS_RECEIPT_DISCREPANCY: Inbound -> Issue (supplier) -> FINISH

Rules:
- Do not re-run an agent that already reported SUCCESS or NOT_APPLICABLE.
- Tendering must run before Dispatching.
- Do not expedite when stock recovered the order line.
"""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("placeholder", "{messages}"),
    ("system",
     "Progress so far:\n{progress}\n\n"
     "Who should act next? Choose one of: {options}. "
     "Choose FINISH if the incident is resolved or needs a human."),
])


def _parse_result(payload: Any) -> AgentResult | None:
    if not payload:
        return None
    try:
        return AgentResult(**payload)
    except Exception:  # noqa: BLE001
        return None


def stock_recovered(agent_results: dict[str, Any] | None) -> bool:
    result = _parse_result((agent_results or {}).get("Smart_Stock_Agent"))
    if result is None:
        return False
    if result.order_still_short is False:
        return True
    return bool(result.replenished) and result.order_still_short is not True


def required_agents(
    event_type: str,
    agent_results: dict[str, Any] | None = None,
) -> list[str]:
    """Agents that must reach SUCCESS or NOT_APPLICABLE for the incident to resolve."""
    if event_type == INBOUND_EVENT:
        return [INBOUND_AGENT, "Issue_Resolution_Agent"]
    if event_type in TRANSPORT_ONLY:
        return ["Tendering_Agent", "Dispatching_Agent", "Issue_Resolution_Agent"]
    if stock_recovered(agent_results):
        return ["Smart_Stock_Agent", "Issue_Resolution_Agent"]
    return list(OUTBOUND_MEMBERS)


def _progress(state: LogisticsState) -> str:
    """Render the structured results so routing is based on data, not prose."""
    results = state.get("agent_results") or {}
    if not results:
        return "No agent has run yet."

    lines = []
    for name in members:
        payload = results.get(name)
        if not payload:
            lines.append(f"- {name}: not yet run")
            continue
        result = _parse_result(payload)
        if result:
            extra = ""
            if result.order_still_short is not None:
                extra += f" short={result.order_still_short}"
            if result.replenished:
                extra += " replenished"
            lines.append(
                f"- {name}: {result.status.value} (confidence {result.confidence:.2f})"
                f"{extra} — {result.summary[:200]}"
            )
        else:
            lines.append(f"- {name}: {str(payload)[:160]}")
    return "\n".join(lines)


def _deterministic_route(state: LogisticsState) -> tuple[str, str] | None:
    """Hard rules that must not be left to model discretion."""
    results = state.get("agent_results") or {}

    def status_of(name: str) -> AgentStatus | None:
        parsed = _parse_result(results.get(name))
        return parsed.status if parsed else None

    if state.get("step_count", 0) >= settings.workflow_max_steps:
        return FINISH, f"Workflow step cap ({settings.workflow_max_steps}) reached."

    event_type = (state.get("event") or {}).get("event_type") or ""
    required = required_agents(event_type, results)
    terminal = {AgentStatus.SUCCESS, AgentStatus.NOT_APPLICABLE}

    if all((status_of(m) in terminal) for m in required):
        return FINISH, "All required specialists completed successfully."

    for name in members:
        if status_of(name) is AgentStatus.NEEDS_HUMAN:
            return FINISH, f"{name} requires a human decision; halting for approval."
        if status_of(name) is AgentStatus.FAILED:
            return FINISH, f"{name} failed; escalating rather than retrying."

    def pending(name: str) -> bool:
        return status_of(name) is None

    if event_type == INBOUND_EVENT:
        if pending(INBOUND_AGENT):
            return INBOUND_AGENT, "Inbound goods-receipt discrepancy."
        if pending("Issue_Resolution_Agent"):
            return "Issue_Resolution_Agent", "GR posted; notify the supplier."
        return FINISH, "Inbound path complete."

    if event_type in TRANSPORT_ONLY:
        if pending("Tendering_Agent"):
            return "Tendering_Agent", "Transport-only event; stock correction is not required."
        if pending("Dispatching_Agent"):
            return "Dispatching_Agent", "Carrier recommended; book it."
        if pending("Issue_Resolution_Agent"):
            return "Issue_Resolution_Agent", "Transport re-booked; notify the customer."
        return FINISH, "Transport path complete."

    # Default / STOCK_MISMATCH
    if pending("Smart_Stock_Agent"):
        return "Smart_Stock_Agent", "Establish inventory truth before any transport spend."

    if stock_recovered(results):
        if pending("Issue_Resolution_Agent"):
            return "Issue_Resolution_Agent", "Pick face recovered in-house; skip expedite."
        return FINISH, "Stock recovered; no TMS work required."

    if pending("Tendering_Agent"):
        return "Tendering_Agent", "Order still short; a carrier recommendation is needed."
    if pending("Dispatching_Agent"):
        return "Dispatching_Agent", "Book the recommended carrier."
    if pending("Issue_Resolution_Agent"):
        return "Issue_Resolution_Agent", "Notify the customer of the new booking."
    return FINISH, "Outbound disruption path complete."


async def supervisor_node(state: LogisticsState, config: RunnableConfig):
    forced = _deterministic_route(state)
    if forced:
        next_agent, reason = forced
        return {"next_agent": next_agent, "routing_reason": reason,
                "step_count": 1,
                "halt_reason": reason if next_agent == FINISH else ""}

    llm = get_llm(config, tier="default")
    chain = prompt | llm.with_structured_output(RouteResponse, method="function_calling")

    try:
        response = await chain.ainvoke(
            {
                "messages": state.get("messages", []),
                "progress": _progress(state),
                "options": ", ".join(options),
            },
            config,
        )
        next_agent, reason = response.next_agent, response.reason
    except Exception as exc:  # noqa: BLE001
        next_agent = FINISH
        reason = f"Routing failed ({type(exc).__name__}: {exc}); halting for a human."

    results = state.get("agent_results") or {}
    if next_agent in results:
        already = next_agent
        try:
            if AgentResult(**results[already]).status in (
                AgentStatus.SUCCESS, AgentStatus.NOT_APPLICABLE,
            ):
                next_agent = FINISH
                reason = f"{already} already finished; nothing further to delegate."
        except Exception:  # noqa: BLE001
            pass

    return {
        "next_agent": next_agent,
        "routing_reason": reason,
        "step_count": 1,
        "llm_calls": 1,
        "halt_reason": reason if next_agent == FINISH else "",
    }
