"""Feature/Expectation: HITL hold typing — no ERP required."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.execution.services import hitl_kind  # noqa: E402


def test_delay_hold_is_not_po():
    """Feature: HITL delay classification
    Expectation: AWAITING_HUMAN with slip_days is kind delay, not po.
    """
    snap = {
        "transaction": {"stage": "AWAITING_HUMAN", "status": "AWAITING_HUMAN", "context": {"slip_days": 14}},
        "match": {},
    }
    assert hitl_kind(snap) == "delay"


def test_po_pending_is_po():
    """Feature: HITL PO classification
    Expectation: PO_PENDING_APPROVAL is kind po.
    """
    snap = {
        "transaction": {"stage": "PO_PENDING_APPROVAL", "status": "AWAITING_HUMAN", "context": {"pending_po": True}},
        "match": {},
    }
    assert hitl_kind(snap) == "po"


def test_match_break_is_match():
    """Feature: HITL match classification
    Expectation: Match BREAK snapshot is kind match.
    """
    snap = {
        "transaction": {"stage": "AWAITING_HUMAN", "status": "AWAITING_HUMAN", "context": {"match_variance_pct": 12}},
        "match": {"status": "BREAK"},
    }
    assert hitl_kind(snap) == "match"


def test_model_hold_is_model():
    """Feature: HITL model classification
    Expectation: hitl_kind=model in context is kind model, not delay.
    """
    snap = {
        "transaction": {
            "stage": "AWAITING_HUMAN",
            "status": "AWAITING_HUMAN",
            "context": {"hitl_kind": "model", "hitl_request_id": "req-1", "slip_days": 14},
        },
        "match": {},
    }
    assert hitl_kind(snap) == "model"


def test_model_options_reject_unknown():
    """Feature: Model HITL option validation
    Expectation: An option not in the request is rejected.
    """
    from agents.execution.hitl import validate_option

    req = {"options": [{"id": "proceed", "label": "Go"}, {"id": "cancel", "label": "Stop"}]}
    bad = validate_option(req, "yeet")
    assert bad.get("error") is True
    assert validate_option(req, "proceed") == "proceed"


def test_model_options_default_when_empty():
    """Feature: Model HITL default options
    Expectation: Missing options become proceed / keep_waiting / cancel.
    """
    from agents.execution.hitl import DEFAULT_OPTIONS, normalize_options, option_ids

    opts = normalize_options(None)
    assert option_ids(opts) == option_ids(DEFAULT_OPTIONS)
    assert "proceed" in option_ids(opts)


def test_partial_wait_does_not_park():
    """Feature: Model HITL park filter
    Expectation: PARTIAL (waiting on an ack) does not create a model request.
    """
    from agents.contracts import AgentResult, AgentStatus
    from agents.execution.hitl import should_park_result

    waiting = AgentResult(status=AgentStatus.PARTIAL, summary="waiting", confidence=0.4)
    failed = AgentResult(status=AgentStatus.FAILED, summary="blocked", confidence=0.3)
    human = AgentResult(status=AgentStatus.NEEDS_HUMAN, summary="unsure", confidence=0.2)
    assert should_park_result(waiting) is False
    assert should_park_result(failed) is True
    assert should_park_result(human) is True


def test_layer2_mcp_is_hitl_only():
    """Feature: Layer 2 MCP catalog
    Expectation: Only hitl.request is listed (Twin keeps world reads).
    """
    from agents.execution.hitl_mcp import ALIASES, list_tools

    names = {t["name"] for t in list_tools()}
    assert names == {"hitl.request"}
    assert ALIASES["request_human"] == "hitl.request"


def test_waiting_union_includes_model_request(monkeypatch):
    """Feature: Overview waiting union
    Expectation: Open model HITL rows appear with kind, question, and options.
    """
    from agents.execution import hitl as hitl_mod

    monkeypatch.setattr(
        "agents.execution.store.list_open_hitl_requests",
        lambda limit=40: [
            {
                "request_id": "req-model",
                "transaction_id": None,
                "kind": "model",
                "question": "Which vendor?",
                "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                "status": "open",
                "recommended_option": "a",
            }
        ],
    )
    rows = hitl_mod.waiting_rows([
        {"transaction_id": "tx-1", "status": "AWAITING_HUMAN", "stage": "AWAITING_HUMAN", "halt_reason": "slip"},
        {"transaction_id": "tx-ok", "status": "SETTLED", "stage": "SETTLED"},
    ])
    kinds = {r.get("kind") for r in rows}
    assert "delay" in kinds or "model" in kinds
    model = next(r for r in rows if r.get("request_id") == "req-model")
    assert model["kind"] == "model"
    assert model["question"] == "Which vendor?"
    assert model["options"][0]["id"] == "a"
    assert model["decide_id"] == "req-model"


def test_create_requires_question():
    """Feature: HITL request validation
    Expectation: Empty question is rejected without touching ERP.
    """
    from agents.execution.hitl import create_hitl_request

    out = create_hitl_request(question="   ")
    assert out.get("error") is True


def test_request_human_tool_name():
    """Feature: request_human tool
    Expectation: LangChain tool is named request_human.
    """
    from agents.tools.execution_tools import request_human

    assert request_human.name == "request_human"
