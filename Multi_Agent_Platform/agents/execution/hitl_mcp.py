"""Layer 2 MCP catalog: model HITL only. Twin MCP stays the world-read door."""

from __future__ import annotations

from typing import Any

from agents.execution.hitl import create_hitl_request

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "hitl.request",
        "description": (
            "Create a model HITL request when a specialist is unsure. "
            "Parks a P2P job when transaction_id is set. Constrained options only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "transaction_id": {"type": "string"},
                "agent_name": {"type": "string"},
                "step": {
                    "type": "string",
                    "enum": ["sense", "decide", "act", "verify", "recommend"],
                    "default": "decide",
                },
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                        },
                        "required": ["id"],
                    },
                },
                "recommended_option": {"type": "string"},
                "rationale": {"type": "string"},
                "confidence": {"type": "number"},
                "workflow": {"type": "string", "enum": ["p2p", "disruption"], "default": "p2p"},
            },
            "required": ["question"],
        },
    },
]

ALIASES = {
    "request_human": "hitl.request",
}


def list_tools() -> list[dict[str, Any]]:
    return list(TOOL_DEFS)


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    arguments = dict(arguments or {})
    mapped = ALIASES.get(name, name)
    if mapped != "hitl.request":
        raise KeyError(f"Unknown MCP tool {name}")
    return create_hitl_request(
        question=arguments.get("question") or "",
        transaction_id=arguments.get("transaction_id") or None,
        workflow=arguments.get("workflow") or "p2p",
        agent_name=arguments.get("agent_name") or "",
        step=arguments.get("step") or "decide",
        options=arguments.get("options"),
        recommended_option=arguments.get("recommended_option"),
        rationale=arguments.get("rationale") or "",
        confidence=arguments.get("confidence"),
        evidence=arguments.get("evidence"),
        context=arguments.get("context"),
        park=arguments.get("park", True),
    )
