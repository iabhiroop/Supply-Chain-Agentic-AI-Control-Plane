"""Feature/Expectation: Eval gate CLI — skip live judge; skip-execute needs Postgres."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from eval_platform.gates import DEFAULT_POLICY, check_gate  # noqa: E402


def test_gate_policy_has_hard_blockers():
    """Feature: Gate policy
    Expectation: Default hard metrics include hard_blockers and schema.
    """
    assert "hard_blockers" in DEFAULT_POLICY["hard"]
    assert "schema" in DEFAULT_POLICY["hard"]


@pytest.mark.skip(reason="product gap / CI: live LLM judge is not run in unit tests")
def test_live_judge_gate():
    """Feature: Live judge gate
    Expectation: cli gate --judge scores with a live model (not implemented in CI).
    """
    raise AssertionError("unreachable")


@pytest.mark.skipif(
    os.getenv("EVAL_RUN_GATE") != "1",
    reason="cli gate --skip-execute needs logistics_scm; set EVAL_RUN_GATE=1 on a laptop with Postgres",
)
def test_gate_skip_execute():
    """Feature: Gate skip-execute
    Expectation: check_gate(execute=False) returns a verdict dict without re-running Layer 2 jobs.
    """
    report = check_gate(judge_llm=False, execute=False)
    assert "passed" in report
    assert report["executed"] is False
    assert report["verdict"] in {"PASS", "FAIL"}
