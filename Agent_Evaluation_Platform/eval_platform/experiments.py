"""Golden datasets and experiment runner."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from psycopg.types.json import Json

from eval_platform.collect_layer2 import ingest_latest_disruption, ingest_snapshot, ingest_transaction
from eval_platform.db import fetch_all, fetch_one, get_conn
from eval_platform.scorers.rules import case_passed

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "p2p_golden_v1.json")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def seed_golden() -> dict[str, str]:
    blob = json.load(open(DATA, encoding="utf-8"))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO datasets (dataset_id, name, description)
            VALUES (%s,%s,%s)
            ON CONFLICT (dataset_id) DO UPDATE SET name = EXCLUDED.name
            """,
            (blob["dataset_id"], blob["name"], blob.get("description")),
        )
        version_id = f"{blob['dataset_id']}-{blob['version']}"
        conn.execute(
            """
            INSERT INTO dataset_versions (version_id, dataset_id, version)
            VALUES (%s,%s,%s)
            ON CONFLICT (version_id) DO NOTHING
            """,
            (version_id, blob["dataset_id"], blob["version"]),
        )
        for item in blob["items"]:
            conn.execute(
                """
                INSERT INTO dataset_items (item_id, version_id, name, input, expected, tags)
                VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (item_id) DO UPDATE SET
                        expected = EXCLUDED.expected,
                        input = EXCLUDED.input,
                        tags = EXCLUDED.tags,
                        name = EXCLUDED.name
                """,
                (
                    item["item_id"],
                    version_id,
                    item["name"],
                    Json(item["input"]),
                    Json(item["expected"]),
                    item.get("tags") or [],
                ),
            )
    return {"dataset_id": blob["dataset_id"], "version_id": f"{blob['dataset_id']}-{blob['version']}"}


def list_datasets() -> list[dict]:
    return fetch_all(
        """
        SELECT d.dataset_id, d.name, v.version_id, v.version,
               (SELECT COUNT(*) FROM dataset_items i WHERE i.version_id = v.version_id) AS items
          FROM datasets d
          JOIN dataset_versions v ON v.dataset_id = d.dataset_id
         ORDER BY v.created_at DESC
        """
    )


def list_items(version_id: str) -> list[dict]:
    return fetch_all("SELECT * FROM dataset_items WHERE version_id = %s", (version_id,))


def _score_item(item: dict, *, judge_llm: bool) -> dict[str, Any]:
    expected = item.get("expected") or {}
    inp = item.get("input") or {}
    fixture = inp.get("fixture")
    if fixture:
        path = fixture if os.path.isabs(fixture) else os.path.join(DATA_DIR, fixture)
        snapshot = json.load(open(path, encoding="utf-8"))
        ingested = ingest_snapshot(
            snapshot, item_id=item["item_id"], expected=expected, judge_llm=judge_llm
        )
    elif inp.get("kind") == "disruption":
        ingested = ingest_latest_disruption(
            expected=expected, judge_llm=judge_llm, item_id=item["item_id"]
        )
    else:
        tx_id = inp.get("transaction_id")
        ingested = ingest_transaction(tx_id, expected=expected, judge_llm=judge_llm)
    ok = case_passed(ingested["scores"], expected)
    metrics = {s["metric"]: {"value": s["value"], "passed": s["passed"]} for s in ingested["scores"]}
    return {
        "ok": ok,
        "metrics": metrics,
        "run_id": ingested["run_id"],
        "trace_id": ingested["trace_id"],
    }


def run_experiment(name: str, version_id: str | None = None, *, judge_llm: bool = False) -> dict[str, Any]:
    seed = seed_golden()
    version_id = version_id or seed["version_id"]
    items = list_items(version_id)
    experiment_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO experiments (experiment_id, name, dataset_version_id, status, config)
            VALUES (%s,%s,%s,'RUNNING',%s)
            """,
            (experiment_id, name, version_id, Json({"judge_llm": judge_llm})),
        )
    results = []
    passed = 0
    for item in items:
        try:
            scored = _score_item(item, judge_llm=judge_llm)
            ok = scored["ok"]
            metrics = scored["metrics"]
            run_id = scored["run_id"]
        except Exception as exc:  # noqa: BLE001
            ok = False
            metrics = {"error": {"value": 0, "passed": False, "rationale": str(exc)}}
            run_id = None
        if ok:
            passed += 1
        result_id = str(uuid.uuid4())
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO experiment_results (result_id, experiment_id, item_id, run_id, passed, metrics)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (result_id, experiment_id, item["item_id"], run_id, ok, Json(metrics)),
            )
        results.append({"item_id": item["item_id"], "name": item["name"], "passed": ok, "metrics": metrics})
    with get_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status = 'DONE', finished_at = now() WHERE experiment_id = %s",
            (experiment_id,),
        )
    return {
        "experiment_id": experiment_id,
        "passed": passed,
        "total": len(items),
        "results": results,
    }


def get_experiment(experiment_id: str) -> dict[str, Any] | None:
    exp = fetch_one("SELECT * FROM experiments WHERE experiment_id = %s", (experiment_id,))
    if not exp:
        return None
    exp["results"] = fetch_all(
        "SELECT * FROM experiment_results WHERE experiment_id = %s", (experiment_id,)
    )
    return exp


def compare(a: str, b: str) -> dict[str, Any]:
    left = get_experiment(a) or {"results": []}
    right = get_experiment(b) or {"results": []}
    by_a = {r["item_id"]: r for r in left["results"]}
    by_b = {r["item_id"]: r for r in right["results"]}
    rows = []
    for item_id in sorted(set(by_a) | set(by_b)):
        pa = (by_a.get(item_id) or {}).get("passed")
        pb = (by_b.get(item_id) or {}).get("passed")
        if pa == pb:
            verdict = "tie"
        elif pb and not pa:
            verdict = "b_wins"
        elif pa and not pb:
            verdict = "a_wins"
        else:
            verdict = "unknown"
        rows.append({"item_id": item_id, "a_passed": pa, "b_passed": pb, "verdict": verdict})
    return {"a": a, "b": b, "cases": rows}
