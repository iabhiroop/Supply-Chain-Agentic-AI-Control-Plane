"""Model-originated HITL requests. Policy holds (delay / po / match) stay in services.py."""

from __future__ import annotations

import uuid
from typing import Any

from agents.config.db import get_conn
from agents.contracts import AgentResult, AgentStatus, HitlOption, HitlRequest
from agents.execution import store

DEFAULT_OPTIONS: list[dict[str, str]] = [
    {"id": "proceed", "label": "Proceed as recommended"},
    {"id": "keep_waiting", "label": "Keep waiting"},
    {"id": "cancel", "label": "Cancel"},
]

VALID_STEPS = {"sense", "decide", "act", "verify", "recommend"}


def normalize_options(raw: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for item in raw or []:
        if isinstance(item, HitlOption):
            options.append({"id": item.id, "label": item.label})
            continue
        if isinstance(item, str):
            options.append({"id": item, "label": item.replace("_", " ").title()})
            continue
        if isinstance(item, dict) and item.get("id"):
            options.append({"id": str(item["id"]), "label": str(item.get("label") or item["id"])})
    return options or list(DEFAULT_OPTIONS)


def option_ids(options: list[dict[str, str]]) -> set[str]:
    return {str(o.get("id")) for o in options if o.get("id")}


def validate_option(request: dict[str, Any], option: str | None) -> str | dict[str, Any]:
    opts = request.get("options") or []
    if isinstance(opts, str):
        import json

        try:
            opts = json.loads(opts)
        except json.JSONDecodeError:
            opts = []
    allowed = option_ids(normalize_options(opts))
    chosen = (option or "").strip()
    if not chosen:
        return {"error": True, "detail": "Model HITL requires an option id."}
    if chosen not in allowed:
        return {"error": True, "detail": f"Unknown option {chosen}. Use one of: {sorted(allowed)}."}
    return chosen


def request_to_dict(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    opts = row.get("options") or []
    if not isinstance(opts, list):
        opts = []
    return {
        **row,
        "options": normalize_options(opts),
        "evidence": row.get("evidence") or [],
        "context": row.get("context") or {},
        "decide_id": row.get("transaction_id") or row.get("request_id"),
        "kind": row.get("kind") or "model",
    }


def create_hitl_request(
    *,
    question: str,
    transaction_id: str | None = None,
    workflow: str = "p2p",
    agent_name: str = "",
    step: str = "decide",
    options: Any = None,
    recommended_option: str | None = None,
    rationale: str = "",
    confidence: float | None = None,
    evidence: list[str] | None = None,
    context: dict[str, Any] | None = None,
    park: bool = True,
) -> dict[str, Any]:
    """Persist a model HITL request. Parks a P2P job when transaction_id is set."""
    q = (question or "").strip()
    if not q:
        return {"error": True, "detail": "question is required"}
    step_name = (step or "decide").strip().lower()
    if step_name not in VALID_STEPS:
        step_name = "decide"
    opts = normalize_options(options)
    rec = (recommended_option or "").strip() or None
    if rec and rec not in option_ids(opts):
        rec = opts[0]["id"]
    request_id = str(uuid.uuid4())
    resume_stage = None
    halt = q
    if transaction_id and park:
        try:
            snap = store.get_snapshot(transaction_id)
            tx = snap.get("transaction") or {}
            resume_stage = tx.get("stage")
            if resume_stage in {"AWAITING_HUMAN", "PO_PENDING_APPROVAL"}:
                resume_stage = (tx.get("context") or {}).get("resume_stage") or "INTENT"
        except KeyError:
            return {"error": True, "detail": f"Unknown transaction {transaction_id}"}
    ctx = {
        **(context or {}),
        "hitl_kind": "model",
        "hitl_request_id": request_id,
        "resume_stage": resume_stage,
    }
    row = {
        "request_id": request_id,
        "transaction_id": transaction_id,
        "workflow": workflow or "p2p",
        "kind": "model",
        "agent_name": agent_name or "",
        "step": step_name,
        "question": q,
        "options": opts,
        "recommended_option": rec,
        "rationale": rationale or "",
        "confidence": float(confidence) if confidence is not None else 0.0,
        "evidence": list(evidence or []),
        "context": ctx,
        "status": "open",
    }
    with get_conn() as conn:
        stored = store.insert_hitl_request(conn, row)
        if transaction_id and park:
            store.set_stage(
                conn,
                transaction_id,
                "AWAITING_HUMAN",
                status="AWAITING_HUMAN",
                halt_reason=halt,
                context_patch={
                    "hitl_kind": "model",
                    "hitl_request_id": request_id,
                    "resume_stage": resume_stage,
                },
                event="MODEL_HITL",
            )
    out = request_to_dict(stored) or request_to_dict(row)
    out["ok"] = True
    out["parked"] = bool(transaction_id and park)
    return out


def open_request_for(transaction_id: str | None = None, request_id: str | None = None) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = store.get_open_hitl_request(conn, transaction_id=transaction_id, request_id=request_id)
    return request_to_dict(row)


def waiting_rows(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union policy-held jobs with open model HITL requests for the inbox."""
    by_tx: dict[str, dict[str, Any]] = {}
    for tx in transactions:
        tid = str(tx.get("transaction_id") or "")
        hold = tx.get("status") in {"AWAITING_HUMAN", "EXCEPTION"} or tx.get("stage") in {
            "AWAITING_HUMAN",
            "PO_PENDING_APPROVAL",
            "EXCEPTION",
        }
        if hold and tid:
            by_tx[tid] = {
                **tx,
                "kind": "po" if tx.get("stage") == "PO_PENDING_APPROVAL" else "delay",
                "decide_id": tid,
                "question": tx.get("halt_reason") or "Human approval required.",
                "options": [],
            }
    for req in store.list_open_hitl_requests():
        packed = request_to_dict(req) or {}
        packed["kind"] = packed.get("kind") or "model"
        packed["status"] = packed.get("tx_status") or "AWAITING_HUMAN"
        packed["stage"] = packed.get("stage") or "AWAITING_HUMAN"
        packed["halt_reason"] = packed.get("question")
        packed["decide_id"] = packed.get("transaction_id") or packed.get("request_id")
        tid = str(packed.get("transaction_id") or "")
        if tid and tid in by_tx:
            by_tx[tid] = {**by_tx[tid], **packed, "kind": "model"}
        elif tid:
            by_tx[tid] = packed
        else:
            by_tx[f"req:{packed.get('request_id')}"] = packed
    return list(by_tx.values())


def should_park_result(result: AgentResult) -> bool:
    if result.status in (AgentStatus.PARTIAL, AgentStatus.SUCCESS, AgentStatus.NOT_APPLICABLE):
        return False
    return result.needs_escalation()


def park_from_agent_results(transaction_id: str, agent_results: dict[str, Any]) -> dict[str, Any] | None:
    """If a specialist is unsure and the job is not a policy hold, persist a model request."""
    try:
        snap = store.get_snapshot(transaction_id)
    except KeyError:
        return None
    tx = snap.get("transaction") or {}
    ctx = tx.get("context") or {}
    from agents.execution.services import hitl_kind

    kind = hitl_kind(snap)
    if kind in {"delay", "po", "match"}:
        return None
    if ctx.get("hitl_kind") == "model" and (tx.get("status") == "AWAITING_HUMAN"):
        return None
    if ctx.get("human_approved_model") and ctx.get("hitl_answer") == "proceed":
        return None
    for name, payload in (agent_results or {}).items():
        try:
            result = AgentResult(**payload) if not isinstance(payload, AgentResult) else payload
        except Exception:  # noqa: BLE001
            continue
        if not should_park_result(result):
            continue
        return create_hitl_request(
            question=result.summary or f"{name} needs a human decision.",
            transaction_id=transaction_id,
            workflow="p2p",
            agent_name=name,
            step="decide",
            options=DEFAULT_OPTIONS,
            recommended_option="proceed",
            rationale=result.recommendation or result.summary,
            confidence=result.confidence,
            evidence=list(result.evidence or result.findings or []),
            context={"agent_status": result.status.value},
        )
    return None


def answer_model_request(
    request: dict[str, Any],
    *,
    option: str,
    note: str | None = None,
    action: str = "answer",
) -> dict[str, Any]:
    checked = validate_option(request, option)
    if isinstance(checked, dict):
        return checked
    request_id = request["request_id"]
    transaction_id = request.get("transaction_id")
    with get_conn() as conn:
        store.close_hitl_request(
            conn,
            request_id,
            status="cancelled" if checked == "cancel" else "answered",
            selected_option=checked,
            human_note=note,
        )
        if transaction_id:
            store.merge_context(
                conn,
                transaction_id,
                {
                    "human_decision": action,
                    "hitl_answer": checked,
                    "hitl_kind": "model",
                    "hitl_request_id": request_id,
                },
            )
    if checked == "keep_waiting":
        return {
            "ok": True,
            "action": "keep_waiting",
            "kind": "model",
            "resumed": False,
            "option": checked,
            "request_id": request_id,
            "status": "AWAITING_HUMAN",
        }
    if checked == "cancel":
        return {
            "ok": True,
            "action": "cancel",
            "kind": "model",
            "resumed": False,
            "option": checked,
            "request_id": request_id,
            "transaction_id": transaction_id,
            "needs_cancel": bool(transaction_id),
        }
    if transaction_id:
        resume_stage = (request.get("context") or {}).get("resume_stage") or "INTENT"
        with get_conn() as conn:
            store.set_stage(
                conn,
                transaction_id,
                resume_stage if resume_stage not in {"AWAITING_HUMAN", "PO_PENDING_APPROVAL"} else "INTENT",
                status="OPEN",
                halt_reason=None,
                context_patch={"human_approved_model": True, "hitl_answer": checked},
                event="HUMAN_ANSWERED_MODEL",
            )
    return {
        "ok": True,
        "action": "answer",
        "kind": "model",
        "resumed": bool(transaction_id),
        "option": checked,
        "request_id": request_id,
        "transaction_id": transaction_id,
    }


def as_contract(row: dict[str, Any]) -> HitlRequest:
    packed = request_to_dict(row) or row
    return HitlRequest(
        request_id=str(packed.get("request_id") or ""),
        transaction_id=packed.get("transaction_id"),
        workflow=packed.get("workflow") or "p2p",
        kind=packed.get("kind") or "model",
        agent_name=packed.get("agent_name") or "",
        step=packed.get("step") or "decide",
        question=packed.get("question") or "",
        options=[HitlOption(**o) for o in packed.get("options") or []],
        recommended_option=packed.get("recommended_option"),
        rationale=packed.get("rationale") or "",
        confidence=float(packed.get("confidence") or 0.0),
        evidence=list(packed.get("evidence") or []),
        context=packed.get("context") or {},
        status=packed.get("status") or "open",
        selected_option=packed.get("selected_option"),
        human_note=packed.get("human_note"),
    )
