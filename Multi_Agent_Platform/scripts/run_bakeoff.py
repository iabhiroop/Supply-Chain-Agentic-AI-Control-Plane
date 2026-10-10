"""Compare LangGraph / Crew / MCP / A2A on the Taiwan golden. Same tools, same checks.

    python scripts/run_bakeoff.py
    python scripts/run_bakeoff.py --runtime mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.disruption.checks import report, verify_recommendation  # noqa: E402
from agents.runtimes.dispatch import RUNTIMES, run  # noqa: E402


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=RUNTIMES, action="append")
    parser.add_argument("--case", default="abc_shutdown")
    args = parser.parse_args(argv)
    runtimes = args.runtime or list(RUNTIMES)
    failed = 0
    for rt in runtimes:
        print(f"\n=== runtime={rt} case={args.case} ===")
        result = await run(args.case, runtime=rt)
        rec = result.get("recommendation") or {}
        print(f"  status={result.get('status')} control={result.get('control_plane') or rt}")
        if rec:
            print(json.dumps({k: rec.get(k) for k in ("reallocate", "alternate", "expedite_rfq")}, indent=2, default=str))
            failed += report(f"{rt} disruption checks", verify_recommendation(rec))
        else:
            print(f"  skip/detail={result.get('detail') or result.get('status')}")
            if result.get("status") not in {"SKIPPED", "RECOMMENDED"}:
                failed += 1
    print("\nBake-off done." if failed == 0 else f"\n{failed} check(s) failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
