"""CLI: apply schema, score a job, run the CI gate.

    python -m eval_platform.cli apply
    python -m eval_platform.cli score --tx aaaaaaaa-bbbb-cccc-dddd-000000000001
    python -m eval_platform.cli gate
    python -m eval_platform.cli gate --skip-execute
    python -m eval_platform.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys

from eval_platform.apply_schema import apply
from eval_platform.collect_layer2 import ingest_transaction
from eval_platform.experiments import run_experiment, seed_golden
from eval_platform.gates import check_gate, promote_baseline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval_platform")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply", help="Create eval schema")
    sub.add_parser("seed", help="Load golden dataset v1")
    score = sub.add_parser("score", help="Score one Layer 2 transaction")
    score.add_argument("--tx", required=True)
    score.add_argument("--judge", action="store_true")
    sub.add_parser("experiment", help="Run golden experiment")
    gate = sub.add_parser("gate", help="CI regression gate")
    gate.add_argument("--judge", action="store_true")
    gate.add_argument(
        "--skip-execute",
        action="store_true",
        help="Score leftover ERP + fixtures only (do not re-run Layer 2 golden jobs).",
    )
    promote = sub.add_parser("promote", help="Pin an experiment as the gate baseline")
    promote.add_argument("--experiment", required=True)
    promote.add_argument("--by", default="operator")
    sub.add_parser("serve", help="Run API on EVAL_PORT (default 8002)")
    args = parser.parse_args(argv)

    if args.cmd == "apply":
        apply()
        return 0
    if args.cmd == "seed":
        print(json.dumps(seed_golden()))
        return 0
    if args.cmd == "score":
        apply()
        result = ingest_transaction(args.tx, judge_llm=args.judge)
        print(json.dumps({k: result[k] for k in ("trace_id", "run_id", "scores")}, default=str, indent=2))
        return 0
    if args.cmd == "experiment":
        apply()
        print(json.dumps(run_experiment("cli"), default=str, indent=2))
        return 0
    if args.cmd == "gate":
        apply()
        execute = not args.skip_execute
        report = check_gate(judge_llm=args.judge, execute=execute)
        print(json.dumps(report, default=str, indent=2))
        return 0 if report["passed"] else 1
    if args.cmd == "promote":
        apply()
        print(json.dumps(promote_baseline(args.experiment, approved_by=args.by), default=str, indent=2))
        return 0
    if args.cmd == "serve":
        from eval_platform.main import main as serve

        serve()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
