"""CLI: apply schema, load disruption pack, check facts, serve read API.

    cd Supply_Chain_Digital_Twin
    set PYTHONPATH=.
    python -m twin.cli apply
    python -m twin.cli load disruption
    python -m twin.cli check
    python -m twin.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="twin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply", help="Create twin tables in logistics_scm (scm schema)")
    load = sub.add_parser("load", help="Load a named dataset (additive)")
    load.add_argument("dataset", nargs="?", default="disruption", help="disruption (twin-disruption-v1)")
    check = sub.add_parser("check", help="Assert Taiwan facts + P2P PR-1001 seed")
    check.add_argument(
        "--skip-p2p-seed",
        action="store_true",
        help="Do not call seed_execution (still asserts twin facts and WH-FR-01 if present)",
    )
    sub.add_parser("serve", help="Read API on TWIN_PORT (default 8003), including MCP /mcp")
    args = parser.parse_args(argv)

    if args.cmd == "apply":
        from twin.apply_schema import apply

        apply()
        print("twin schema ready.")
        return 0
    if args.cmd == "load":
        from twin.seed import load

        print(json.dumps(load(args.dataset), default=str, indent=2))
        return 0
    if args.cmd == "check":
        from twin.check import run_check

        report = run_check(verify_p2p_seed=not args.skip_p2p_seed)
        print(json.dumps(report, default=str, indent=2))
        return 0 if report["passed"] else 1
    if args.cmd == "serve":
        from twin.main import main as serve

        serve()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
