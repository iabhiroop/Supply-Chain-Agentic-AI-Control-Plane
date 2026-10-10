"""CI regression gate. Hard blockers fail the build; cost/latency are warnings.

Baselines are pinned explicitly via `python -m eval_platform.cli promote`.
A passing gate does not rewrite the baseline.
"""

from __future__ import annotations

from typing import Any

from eval_platform.db import fetch_one, get_conn
from eval_platform.execute_golden import execute_golden_suite
from eval_platform.experiments import run_experiment, seed_golden
from psycopg.types.json import Json

DEFAULT_POLICY = {
    "hard": [
        "hard_blockers",
        "schema",
        "tool_trajectory",
        "business_rule",
        "business_rule_freight",
        "business_rule_gr",
        "safety_pii",
    ],
    "success_floor": 1.0,
}


def latest_baseline(gate_id: str = "p2p-default") -> dict[str, Any] | None:
    return fetch_one(
        "SELECT * FROM baselines WHERE gate_id = %s ORDER BY created_at DESC LIMIT 1",
        (gate_id,),
    )


def promote_baseline(experiment_id: str, *, approved_by: str = "operator") -> dict[str, Any]:
    exp = fetch_one("SELECT * FROM experiments WHERE experiment_id = %s", (experiment_id,))
    if not exp:
        raise KeyError(experiment_id)
    results = fetch_one(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE passed) AS passed
          FROM experiment_results WHERE experiment_id = %s
        """,
        (experiment_id,),
    ) or {}
    baseline_id = f"base-{experiment_id[:8]}"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO gates (gate_id, name, policy)
            VALUES (%s,%s,%s)
            ON CONFLICT (gate_id) DO UPDATE SET policy = EXCLUDED.policy
            """,
            ("p2p-default", "P2P regression", Json(DEFAULT_POLICY)),
        )
        conn.execute(
            """
            INSERT INTO baselines (baseline_id, gate_id, experiment_id, metrics, approved_by)
            VALUES (%s,'p2p-default',%s,%s,%s)
            ON CONFLICT (baseline_id) DO UPDATE SET
                experiment_id = EXCLUDED.experiment_id,
                metrics = EXCLUDED.metrics,
                approved_by = EXCLUDED.approved_by
            """,
            (
                baseline_id,
                experiment_id,
                Json({"passed": results.get("passed"), "total": results.get("total")}),
                approved_by,
            ),
        )
    return {"baseline_id": baseline_id, "experiment_id": experiment_id, "approved_by": approved_by}


def check_gate(*, judge_llm: bool = False, execute: bool = False) -> dict[str, Any]:
    seed_golden()
    execute_report = None
    if execute:
        execute_report = execute_golden_suite()
    experiment = run_experiment("ci-gate", judge_llm=judge_llm)
    failures = [r for r in experiment["results"] if not r["passed"]]
    warnings = []
    for row in experiment["results"]:
        metrics = row.get("metrics") or {}
        steps = (metrics.get("budget_steps") or {}).get("value") or 0
        if steps and steps > 20:
            warnings.append(f"{row['item_id']}: steps {steps} above soft cap 20")
    passed = experiment["passed"] == experiment["total"] and not failures
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO gates (gate_id, name, policy)
            VALUES (%s,%s,%s)
            ON CONFLICT (gate_id) DO UPDATE SET policy = EXCLUDED.policy
            """,
            ("p2p-default", "P2P regression", Json(DEFAULT_POLICY)),
        )
    baseline = latest_baseline()
    return {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "experiment_id": experiment["experiment_id"],
        "failures": failures,
        "warnings": warnings,
        "summary": f"{experiment['passed']}/{experiment['total']} cases passed",
        "executed": bool(execute),
        "execute_report": execute_report,
        "baseline_id": (baseline or {}).get("baseline_id"),
        "baseline_note": "Pin with `python -m eval_platform.cli promote --experiment <id>`.",
    }
