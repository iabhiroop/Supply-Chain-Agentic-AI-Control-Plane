"""python -m os_platform.cli serve | stack"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="os_platform")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="BFF + React on OS_PORT (default 8010)")
    stack = sub.add_parser("stack", help="Start Twin, Layer 2, GraphRAG, Eval, OS")
    stack.add_argument(
        "--attach",
        action="store_true",
        help="Run the OS BFF in this process after starting the other layers.",
    )
    args = parser.parse_args(argv)
    if args.cmd == "serve":
        from os_platform.main import main as serve

        serve()
        return 0
    if args.cmd == "stack":
        from os_platform.stack import run

        return run(attach_os=args.attach)
    return 1


if __name__ == "__main__":
    sys.exit(main())
