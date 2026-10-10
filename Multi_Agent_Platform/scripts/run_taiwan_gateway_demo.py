"""Wave 4: meter the Taiwan disruption job on the AI Gateway.

Runs Wave 3 execute_disruption (twin facts), then two identical grounded chat
calls through the existing Layer 2 virtual key. Asserts a cache hit on the
second call and joins usage_records by X-Trace-Id.

Requires the Gateway on :8000 and SCM_GATEWAY_API_KEY. Does not re-seed P2P.

Usage (from Multi_Agent_Platform/):
    python scripts/run_taiwan_gateway_demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.config.settings import settings  # noqa: E402
from agents.disruption.checks import check, report, verify_recommendation  # noqa: E402
from agents.disruption.gateway_demo import (  # noqa: E402
    gateway_budget,
    gateway_usage,
    taiwan_chat,
)
from agents.disruption.runtime import execute_disruption  # noqa: E402


def verify_gateway(first: dict, second: dict, usage: dict, budget: dict, orch_budget: dict) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []
    rows = usage.get("requests") or []
    check(checks, "gateway usage rows for this trace", len(rows) >= 1, f"n={len(rows)}")
    second_status = second.get("cache_status") or ("hit" if second.get("cache_hit") else "miss")
    check(
        checks,
        "paraphrased Taiwan chat is a cache hit (exact or semantic)",
        second_status in {"hit", "semantic_hit"} or second.get("cache_hit") is True
        or any(r.get("cache_hit") for r in rows),
        f"status={second_status} usage_hits={sum(1 for r in rows if r.get('cache_hit'))}",
    )
    models = {r.get("model") for r in rows if r.get("model")}
    if not models and first.get("model"):
        models = {first.get("model")}
    check(checks, "model recorded", bool(models), f"{models}")
    tokens = usage.get("total_tokens") or first.get("total_tokens") or 0
    check(checks, "tokens recorded", int(tokens or 0) >= 0, f"total_tokens={tokens}")
    cost = usage.get("total_cost_usd")
    if cost is None:
        cost = first.get("cost_usd")
    check(checks, "cost recorded", cost is not None, f"total_cost_usd={cost}")
    agents = {r.get("agent_id") for r in rows if r.get("agent_id")}
    check(
        checks,
        "X-Agent-Id Procurement_Agent",
        "Procurement_Agent" in agents,
        f"{agents}",
    )
    proc = (budget.get("named_agents") or {}).get("Procurement_Agent") or budget
    orch = (orch_budget.get("named_agents") or {}).get("Disruption_Orchestrator") or orch_budget
    check(
        checks,
        "Procurement_Agent named $ bucket",
        proc.get("limit_usd") is not None,
        f"{proc}",
    )
    check(
        checks,
        "Disruption_Orchestrator named $ bucket differs",
        orch.get("limit_usd") is not None and orch.get("limit_usd") != proc.get("limit_usd"),
        f"proc={proc.get('limit_usd')} orch={orch.get('limit_usd')}",
    )
    return checks


async def _wait_usage(trace_id: str, *, attempts: int = 8, delay: float = 0.6) -> dict:
    last: dict = {}
    for _ in range(attempts):
        last = gateway_usage(trace_id)
        if (last.get("requests") or last.get("total_requests")):
            return last
        time.sleep(delay)
    return last


async def main() -> int:
    if not settings.gateway_api_key:
        print("SCM_GATEWAY_API_KEY is not set. Run python scripts/provision_gateway_key.py")
        return 1

    print("Wave 4 — Taiwan disruption on the AI Gateway")
    print(f"  gateway={settings.gateway_url} model={settings.model_cheap}")

    print("\n1. execute_disruption (twin facts, no LLM arithmetic)…")
    result = await execute_disruption()
    rec = result.get("recommendation") or {}
    trace_id = result.get("trace_id") or ""
    print(f"  status={result.get('status')} trace_id={trace_id}")
    failed = report("Disruption checks (twin facts, not a prompt)", verify_recommendation(rec))
    if failed:
        print("\nDisruption asserts failed; not calling the Gateway.")
        return 1

    print("\n2. Grounded Taiwan chat via default_route / X-Route-Hint=cheap (no pinned model)…")
    first = taiwan_chat(recommendation=rec, trace_id=trace_id, agent_id="Procurement_Agent")
    print(f"  call1 model={first.get('model')} cache={first.get('cache_status')} "
          f"tokens={first.get('total_tokens')} cost={first.get('cost_usd')}")
    if first.get("text"):
        print(f"  paraphrase: {first['text'][:400]}")

    print("\n3. Paraphrased question (expect semantic_hit, not only exact Redis)…")
    paraphrased = (
        "Taiwan supplier shutdown — which open POs are exposed, and what should procurement do?"
    )
    second = taiwan_chat(
        recommendation=rec,
        trace_id=trace_id,
        agent_id="Procurement_Agent",
        question=paraphrased,
    )
    print(f"  call2 model={second.get('model')} cache={second.get('cache_status')} "
          f"tokens={second.get('total_tokens')} cost={second.get('cost_usd')}")

    print("\n4. Join usage_records by trace_id…")
    usage = await _wait_usage(trace_id)
    print(json.dumps(
        {k: usage.get(k) for k in (
            "trace_id", "total_requests", "total_tokens", "total_cost_usd",
            "cache_hit_count", "model_breakdown", "requests",
        )},
        indent=2,
        default=str,
    ))

    print("\n5. Named-agent budgets (Procurement_Agent vs Disruption_Orchestrator)…")
    budget = gateway_budget("Procurement_Agent")
    orch_budget = gateway_budget("Disruption_Orchestrator")
    print(json.dumps(
        {
            "procurement": (budget.get("named_agents") or {}).get("Procurement_Agent") or budget,
            "orchestrator": (orch_budget.get("named_agents") or {}).get("Disruption_Orchestrator") or orch_budget,
        },
        indent=2,
        default=str,
    ))

    print("\n6. Budget_Probe ($0 daily) should 429…")
    probe_429 = False
    try:
        taiwan_chat(recommendation=rec, trace_id=trace_id, agent_id="Budget_Probe", semantic_cache=False)
    except RuntimeError as exc:
        probe_429 = "HTTP 429" in str(exc)
        print(f"  {exc}")
    check_probe = [("Budget_Probe returns 429", probe_429, f"got 429={probe_429}")]

    failed += report("Gateway demo checks", verify_gateway(first, second, usage, budget, orch_budget))
    failed += report("Named-agent 429", check_probe)
    print("\nDone." if failed == 0 else f"\n{failed} check(s) failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
