"""Feature/Expectation: Scorecard rollup — no database."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from eval_platform.scorecard import summarize_scores  # noqa: E402


def test_success_and_correctness():
    """Feature: Scorecard rollup
    Expectation: All-pass scores yield success with correctness 1.0 and summed tokens.
    """
    scores = [
        {"metric": "schema", "passed": True, "value": 1},
        {"metric": "po_1842", "passed": True, "value": 1},
        {"metric": "cover_pct_grounded", "passed": True, "value": 1},
        {"metric": "hard_blockers", "passed": True, "value": 1},
    ]
    card = summarize_scores(scores, trace={"prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.01})
    assert card["success"] is True
    assert card["correctness"] == 1.0
    assert card["groundedness"] == 1.0
    assert card["tools"] == 1.0
    assert card["tokens"] == 15
    assert card["cost_usd"] == 0.01


def test_hard_fail():
    """Feature: Scorecard hard fail
    Expectation: A failed hard_blockers metric makes success false.
    """
    scores = [
        {"metric": "illegal_vendor", "passed": False, "value": 0},
        {"metric": "hard_blockers", "passed": False, "value": 0, "rationale": "wrong vendor"},
    ]
    card = summarize_scores(scores)
    assert card["success"] is False
