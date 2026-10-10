"""Deterministic P2P stage-machine orchestrator.

Happy-path routing is code, not an LLM. The model is consulted only on
EXCEPTION when more than one recovery is already policy-legal.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from agents.config.settings import get_llm, settings
from agents.contracts import AgentResult, AgentStatus, TransactionStage
from agents.execution import policy, store
from agents.graph.state import LogisticsState
from agents.memory.prompts import ORCHESTRATOR_SYSTEM

members = [
    "Sourcing_Agent",
    "Procurement_Agent",
    "Logistics_Agent",
    "Finance_Agent",
]
FINISH = "FINISH"
options = ["FINISH"] + members


class RouteResponse(BaseModel):
    next_agent: Literal[
        "FINISH",
        "Sourcing_Agent",
        "Procurement_Agent",
        "Logistics_Agent",
        "Finance_Agent",
    ] = Field(description="Which specialist acts next, or FINISH.")
    reason: str = Field(default="")


SYSTEM_PROMPT = ORCHESTRATOR_SYSTEM

prompt = ChatPromptTemplate.from_messages([
    ("system", "{orchestrator_system}"),
    ("placeholder", "{messages}"),
    ("system", "Progress:\n{progress}\n\nLegal options: {legal}\nWho next?"),
])


def _parse_result(payload: Any) -> AgentResult | None:
    if not payload:
        return None
    try:
        return AgentResult(**payload)
    except Exception:  # noqa: BLE001
        return None


def _status_of(results: dict, name: str) -> AgentStatus | None:
    parsed = _parse_result(results.get(name))
    return parsed.status if parsed else None


def _progress(state: LogisticsState, snap: dict) -> str:
    tx = snap.get("transaction") or {}
    lines = [f"stage={tx.get('stage')} status={tx.get('status')} pr={tx.get('pr_id')}"]
    results = state.get("agent_results") or {}
    for name in members:
        parsed = _parse_result(results.get(name))
        if parsed:
            lines.append(f"- {name}: {parsed.status.value} verified={parsed.verified} — {parsed.summary[:180]}")
        else:
            lines.append(f"- {name}: not yet run")
    return "\n".join(lines)


def _deterministic_route(state: LogisticsState, snap: dict) -> tuple[str, str] | None:
    results = state.get("agent_results") or {}
    tx = snap["transaction"]
    stage = tx.get("stage") or TransactionStage.INTENT.value
    status = tx.get("status") or "OPEN"

    if state.get("step_count", 0) >= settings.workflow_max_steps:
        return FINISH, f"Workflow step cap ({settings.workflow_max_steps}) reached."

    if status in {"AWAITING_HUMAN", "SETTLED", "CANCELLED"} or stage in {
        TransactionStage.AWAITING_HUMAN.value,
        TransactionStage.SETTLED.value,
        TransactionStage.PO_PENDING_APPROVAL.value,
        "CANCELLED",
    }:
        return FINISH, f"Transaction is {stage}/{status}."

    for name in members:
        st = _status_of(results, name)
        if st is AgentStatus.NEEDS_HUMAN:
            return FINISH, f"{name} requires a human decision."
        if st is AgentStatus.FAILED:
            return FINISH, f"{name} failed; escalating."
        if st is AgentStatus.PARTIAL:
            return FINISH, f"{name} is waiting on an acknowledgement; not advancing."

    quotes = snap.get("quotes") or []
    po = snap.get("po")
    booking = snap.get("booking")
    gr = snap.get("gr")
    match = snap.get("match")
    unread = snap.get("unread") or []

    delay = any(
        (m.get("parsed") or {}).get("type") == "delay"
        or "delayed" in ((m.get("body") or "").lower())
        for m in unread
    )
    if delay or status == "EXCEPTION" or stage == TransactionStage.EXCEPTION.value:
        if (tx.get("context") or {}).get("rebooked"):
            return FINISH, "Delay recovered within slip policy."
        slip = int(((tx.get("context") or {}).get("slip_days")) or 3)
        recoveries = policy.delay_recoveries(slip, snap.get("policy") or {})
        if len(recoveries) > 1:
            return None  # LLM among legal options
        return "Logistics_Agent", "Delay exception — Logistics evaluates slip policy."

    if (tx.get("context") or {}).get("rebooked") and not gr:
        return FINISH, "Delay recovered; waiting on a later tracking event."

    if stage in {TransactionStage.INTENT.value, TransactionStage.SOURCING.value} and not quotes:
        return "Sourcing_Agent", "Purchase intent needs RFQs and normalized quotes."

    if quotes and not po and stage not in {TransactionStage.PO_ISSUED.value, TransactionStage.DISPATCH_READY.value}:
        if _status_of(results, "Procurement_Agent") is AgentStatus.SUCCESS:
            pass
        else:
            return "Procurement_Agent", "Legal quotes are in; issue the PO."

    if po and not gr:
        return "Logistics_Agent", "PO issued; book, consume tracking, post GR."

    if gr and not (match and match.get("status") == "POSTED"):
        return "Finance_Agent", "Goods received; capture invoices and match."

    if match and match.get("status") == "POSTED":
        return FINISH, "Settled."

    if booking and not gr:
        return "Logistics_Agent", "Booking in progress."

    return FINISH, "No further specialist work."


async def supervisor_node(state: LogisticsState, config: RunnableConfig):
    tx_id = state.get("transaction_id") or ""
    snap = store.get_snapshot(tx_id)
    forced = _deterministic_route(state, snap)
    if forced:
        next_agent, reason = forced
        return {
            "next_agent": next_agent,
            "routing_reason": reason,
            "step_count": 1,
            "halt_reason": reason if next_agent == FINISH else "",
            "stage": (snap["transaction"] or {}).get("stage") or "",
        }

    llm = get_llm(config, tier="default")
    chain = prompt | llm.with_structured_output(RouteResponse, method="function_calling")
    legal = [m for m in members if _status_of(state.get("agent_results") or {}, m) is None]
    if not legal:
        legal = list(members)
    from agents.memory.store import active_body

    orchestrator_system = active_body("Orchestrator", "system", workflow="p2p") or SYSTEM_PROMPT
    try:
        response = await chain.ainvoke(
            {
                "orchestrator_system": orchestrator_system,
                "messages": state.get("messages", []),
                "progress": _progress(state, snap),
                "legal": ", ".join(legal + [FINISH]),
            },
            config,
        )
        next_agent, reason = response.next_agent, response.reason
        if next_agent not in legal and next_agent != FINISH:
            next_agent, reason = FINISH, f"Model proposed {response.next_agent} which is not legal."
    except Exception as exc:  # noqa: BLE001
        next_agent = FINISH
        reason = f"Routing failed ({type(exc).__name__}: {exc}); holding for a human."

    return {
        "next_agent": next_agent,
        "routing_reason": reason,
        "step_count": 1,
        "llm_calls": 1,
        "halt_reason": reason if next_agent == FINISH else "",
        "stage": (snap["transaction"] or {}).get("stage") or "",
    }
