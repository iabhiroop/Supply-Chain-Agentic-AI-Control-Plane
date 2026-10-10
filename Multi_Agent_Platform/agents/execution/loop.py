"""Pack a specialist turn into a graph state update."""

from __future__ import annotations

from langchain_core.messages import HumanMessage

from agents.contracts import AgentResult, AgentStatus


def pack_turn(
    name: str,
    result: AgentResult,
    *,
    tool_calls: int = 0,
    facts: dict | None = None,
    workflow: str | None = None,
) -> dict:
    payload = result.model_dump(mode="json")
    payload["_elapsed_ms"] = 0
    payload["_tool_calls"] = tool_calls
    payload["_llm_calls"] = 0
    try:
        from agents.memory.prompts import workflow_for
        from agents.memory.store import active_prompt, record_turn

        wf = workflow_for(name, workflow=workflow)
        prompt = active_prompt(name, "charter", workflow=wf) or active_prompt(
            name, "system", workflow=wf
        )
        payload["_prompt_id"] = (prompt or {}).get("prompt_id")
        payload["_prompt_version"] = (prompt or {}).get("version")
        record_turn(
            agent_name=name,
            result=result,
            tool_calls=tool_calls,
            workflow=workflow,
            facts=facts,
        )
    except Exception:  # noqa: BLE001
        payload["_prompt_id"] = None
        payload["_prompt_version"] = None
    escalations = [f"{name}: {result.summary}"] if result.needs_escalation() else []
    halt = ""
    if result.status in (AgentStatus.NEEDS_HUMAN, AgentStatus.FAILED):
        halt = result.summary
    return {
        "messages": [HumanMessage(content=f"{name} reported:\n{result.brief()}", name=name)],
        "agent_results": {name: payload},
        "completed_agents": [name],
        "step_count": 1,
        "llm_calls": 0,
        "tool_calls": tool_calls,
        "escalations": escalations,
        "halt_reason": halt,
        "stage": "",
    }
