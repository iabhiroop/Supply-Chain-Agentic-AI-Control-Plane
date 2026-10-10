"""Shared sub-agent runner: bounded autonomy plus typed hand-offs.

Each domain sub-agent is a thin declaration (name, charter prompt, tool
allow-list, model tier). All the shared machinery lives here:

* **Least privilege** — the agent only ever sees the tools it declares.
* **Bounded autonomy** — caps on tool calls, LLM calls and wall-clock time; on
  breach the agent halts and hands control back rather than spinning.
* **Typed hand-off** — the free-form ReAct transcript is converted into an
  `AgentResult` by a separate extraction call.

Why two phases rather than `create_react_agent(response_format=...)`: the AI
Gateway does not implement OpenAI's `response_format`, so `json_schema` and
`json_mode` structured output silently return prose. Only tool/function calling
produces reliable structure, so the report step uses
`with_structured_output(..., method="function_calling")` explicitly.
"""

from __future__ import annotations

import asyncio
import time

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from agents.config.settings import get_llm, settings
from agents.contracts import AgentResult, AgentStatus
from agents.graph.state import LogisticsState

REPORT_INSTRUCTION = (
    "Report your work as a structured result.\n"
    "- status: SUCCESS if your charter is fulfilled, PARTIAL if more work is needed, "
    "FAILED if you could not act, NEEDS_HUMAN if a person must decide, "
    "NOT_APPLICABLE if there was nothing for you to do.\n"
    "- findings: facts you established from tool results.\n"
    "- actions_taken: only mutations you actually performed and saw succeed.\n"
    "- evidence: the specific data your conclusion rests on.\n"
    "- confidence: your genuine 0-1 confidence. Use a low value if data was missing "
    "or a tool failed.\n"
    "- order_still_short: true if the order line still cannot be filled from warehouse stock "
    "after your work; false if it can. Null if this does not apply to you.\n"
    "- replenished: true if you transferred stock onto the pick face.\n"
    "- customer_notify: true only if the customer promise is still at risk. False if you "
    "recovered stock in-house or this is an internal/supplier matter.\n"
    "Never claim an action you did not perform."
)


def _count_tool_calls(messages) -> int:
    return sum(
        len(m.tool_calls) for m in messages
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
    )


async def _extract_result(transcript: str, agent_name: str, charter: str) -> AgentResult:
    """Convert a ReAct transcript into a validated AgentResult."""
    llm = get_llm(tier="cheap")
    structured = llm.with_structured_output(AgentResult, method="function_calling")
    try:
        result = await structured.ainvoke(
            f"You are {agent_name}. Your charter: {charter}\n\n"
            f"This is what you just did:\n{transcript}\n\n{REPORT_INSTRUCTION}"
        )
        return result if isinstance(result, AgentResult) else AgentResult(**dict(result))
    except Exception as exc:  # noqa: BLE001
        # Never lose the agent's work to a reporting failure; degrade to a
        # low-confidence result so the orchestrator escalates instead.
        return AgentResult(
            status=AgentStatus.PARTIAL,
            summary=f"Completed work but could not produce a structured report: {exc}",
            confidence=0.0,
        )


async def run_subagent(
    state: LogisticsState,
    config: RunnableConfig,
    *,
    name: str,
    charter: str,
    system_prompt: str,
    tools: list,
    tier: str = "default",
) -> dict:
    """Run one bounded sub-agent turn and return a state update."""
    started = time.time()
    incident_id = state.get("incident_id", "")
    event = state.get("event", {})

    # Scoped context: the agent gets the incident facts and the results of
    # agents that ran before it, not the entire raw message history.
    prior = state.get("agent_results") or {}
    context = [f"Incident {incident_id or 'n/a'}.", f"Event: {event}"]
    for other, payload in prior.items():
        if other == name:
            continue
        try:
            context.append(f"\n{other} already reported:\n{AgentResult(**payload).brief()}")
        except Exception:  # noqa: BLE001
            context.append(f"\n{other} already reported: {payload}")
    task = "\n".join(context)

    # recursion_limit bounds the ReAct loop. Each iteration is roughly one LLM
    # call plus its tool calls, so this is the effective step cap.
    limit = max(4, min(settings.agent_max_tool_calls, settings.agent_max_llm_calls) * 2)
    agent = create_react_agent(
        get_llm(config, tier=tier),
        tools,
        prompt=system_prompt,   # NB: `state_modifier` was removed in langgraph 1.x
        name=name,
    )

    halt_reason = ""
    messages = []
    try:
        outcome = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [HumanMessage(content=task)]},
                config={**(config or {}), "recursion_limit": limit},
            ),
            timeout=settings.agent_timeout_seconds,
        )
        messages = outcome.get("messages", [])
    except GraphRecursionError:
        halt_reason = f"{name} hit its step cap ({limit}) and was halted."
    except asyncio.TimeoutError:
        halt_reason = f"{name} exceeded its {settings.agent_timeout_seconds}s timeout."
    except Exception as exc:  # noqa: BLE001
        halt_reason = f"{name} failed: {type(exc).__name__}: {exc}"

    tool_calls = _count_tool_calls(messages)
    llm_calls = sum(1 for m in messages if isinstance(m, AIMessage))

    if halt_reason:
        result = AgentResult(
            status=AgentStatus.FAILED,
            summary=halt_reason,
            confidence=0.0,
            recommendation="Escalate to a human operator.",
        )
    else:
        transcript_parts = []
        for m in messages:
            role = type(m).__name__.replace("Message", "")
            content = (m.content or "").strip() if isinstance(m.content, str) else str(m.content)
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                for tc in m.tool_calls:
                    transcript_parts.append(f"[tool call] {tc['name']}({tc['args']})")
            if content:
                transcript_parts.append(f"[{role}] {content[:1200]}")
        result = await _extract_result("\n".join(transcript_parts[-40:]), name, charter)
        llm_calls += 1  # the extraction call

    payload = result.model_dump(mode="json")
    payload["_elapsed_ms"] = int((time.time() - started) * 1000)
    payload["_tool_calls"] = tool_calls
    payload["_llm_calls"] = llm_calls
    escalations = [f"{name}: {result.summary}"] if result.needs_escalation() else []

    return {
        "messages": [HumanMessage(content=f"{name} reported:\n{result.brief()}", name=name)],
        "agent_results": {name: payload},
        "completed_agents": [name],
        "step_count": 1,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "escalations": escalations,
    }
