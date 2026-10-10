"""Run and verify the Wave 3 disruption recommendation (beside P2P).

Happy path is deterministic Python + twin facts (HTTP :8003 or SQL fallback).
Does not settle POs and does not call an LLM.

Usage (from Multi_Agent_Platform/):
    python scripts/run_disruption.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.disruption.checks import report, verify_recommendation  # noqa: E402
from agents.disruption.runtime import execute_disruption  # noqa: E402


def verify(rec: dict) -> list[tuple[str, bool, str]]:
    return verify_recommendation(rec)


async def main() -> int:
    print("Running disruption decision spine (VEND-ABC / MAT-SEMI-1)…")
    result = await execute_disruption()
    rec = result.get("recommendation") or {}
    print(f"  status={result.get('status')} tool_calls={result.get('tool_calls')} "
          f"agents={result.get('completed_agents')}")
    if result.get("record_error"):
        print(f"  record_error={result['record_error']}")
    print("\nRecommendation:")
    print(json.dumps(rec, indent=2, default=str))
    if rec.get("summary"):
        print(f"\nSummary: {rec['summary']}")
    failed = report("Disruption checks (twin facts, not a prompt)", verify(rec))
    print("\nDone." if failed == 0 else f"\n{failed} check(s) failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
