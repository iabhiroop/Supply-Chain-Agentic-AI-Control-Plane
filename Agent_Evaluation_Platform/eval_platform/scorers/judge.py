"""LLM-as-judge via the AI Gateway. Never uses a provider SDK."""

from __future__ import annotations

from typing import Any

from eval_platform.config import settings

PROMPT = """You score a purchase-to-pay agent run. Reply with JSON:
{"task_success": 0 or 1, "groundedness": 0-1, "rationale": "one sentence"}
Did the agents solve the business job (quotes, legal PO, movement, match) without violating policy?
"""


def score_run(snapshot: dict, expected: dict | None = None) -> list[dict[str, Any]]:
    if not settings.gateway_api_key:
        return [{
            "scorer": "llm_judge",
            "scorer_version": "judge-1",
            "metric": "judge_skipped",
            "value": 0.0,
            "passed": None,
            "rationale": "SCM_GATEWAY_API_KEY unset; judge skipped.",
        }]
    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=settings.judge_model,
            temperature=0,
            max_tokens=256,
            base_url=f"{settings.gateway_url}/v1",
            api_key=settings.gateway_api_key,
            default_headers={"X-Tenant-Id": settings.tenant_id, "X-Route-Hint": "cheap"},
        )
        tx = snapshot.get("transaction") or {}
        po = snapshot.get("po") or {}
        blob = (
            f"stage={tx.get('stage')} status={tx.get('status')} "
            f"po={po.get('vendor_id')}@{po.get('unit_price')} "
            f"expected={expected}"
        )
        text = llm.invoke(PROMPT + "\n" + blob).content
        import json
        import re

        match = re.search(r"\{.*\}", str(text), re.S)
        data = json.loads(match.group(0)) if match else {}
        success = int(data.get("task_success") or 0)
        grounded = float(data.get("groundedness") or 0)
        rationale = str(data.get("rationale") or text)[:500]
        return [
            {
                "scorer": "llm_judge",
                "scorer_version": f"judge-1:{settings.judge_model}",
                "metric": "judge_task_success",
                "value": float(success),
                "passed": bool(success),
                "rationale": rationale,
            },
            {
                "scorer": "llm_judge",
                "scorer_version": f"judge-1:{settings.judge_model}",
                "metric": "judge_groundedness",
                "value": grounded,
                "passed": grounded >= 0.7,
                "rationale": rationale,
            },
        ]
    except Exception as exc:  # noqa: BLE001
        return [{
            "scorer": "llm_judge",
            "scorer_version": "judge-1",
            "metric": "judge_error",
            "value": 0.0,
            "passed": None,
            "rationale": f"{type(exc).__name__}: {exc}",
        }]
