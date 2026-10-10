"""Simulated ERP document APIs and execution-transaction control plane."""

from __future__ import annotations

import json

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agents.chat.lookup import _clean
from agents.config.db import fetch_all, fetch_one
from agents.execution import services, store
from agents.execution.runtime import execute_transaction

api_router = APIRouter(prefix="/api/execution", tags=["ERP execution"])
ops_router = APIRouter(tags=["Execution"])


class TxBody(BaseModel):
    transaction_id: str
    agent_name: str = "operator"


class HitlCreateBody(BaseModel):
    question: str
    transaction_id: str | None = None
    agent_name: str = ""
    step: str = "decide"
    options: list[dict] | None = None
    recommended_option: str | None = None
    rationale: str = ""
    confidence: float | None = None
    workflow: str = "p2p"


class CreatePoBody(TxBody):
    vendor_id: str
    unit_price: float
    human_approved: bool = False


class DecideBody(BaseModel):
    action: str = "approve_exception"
    wait: bool = True
    option: str | None = None
    note: str | None = None
    request_id: str | None = None


class CancelBody(BaseModel):
    kind: str
    key: str
    transaction_id: str | None = None


def _ok(payload: dict):
    if payload.get("error"):
        raise HTTPException(status_code=400, detail=payload.get("detail") or payload)
    return payload


@api_router.get("/transactions/{transaction_id}")
def get_tx_snapshot(transaction_id: str):
    try:
        return store.get_snapshot(transaction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@api_router.post("/rfq")
def api_issue_rfq(body: TxBody):
    return _ok(services.issue_rfqs(body.transaction_id, agent_name=body.agent_name))


@api_router.post("/po")
def api_create_po(body: CreatePoBody):
    return _ok(services.create_po(
        body.transaction_id,
        vendor_id=body.vendor_id,
        unit_price=body.unit_price,
        agent_name=body.agent_name,
        human_approved=body.human_approved,
    ))


@api_router.post("/booking")
def api_book(body: TxBody):
    return _ok(services.book_shipment(body.transaction_id, agent_name=body.agent_name))


@api_router.post("/tracking")
def api_tracking(body: TxBody):
    return _ok(services.consume_tracking(body.transaction_id, agent_name=body.agent_name))


@api_router.post("/gr")
def api_gr(body: TxBody):
    return _ok(services.post_gr(body.transaction_id, agent_name=body.agent_name))


@api_router.post("/invoices")
def api_invoices(body: TxBody):
    return _ok(services.capture_invoices(body.transaction_id, agent_name=body.agent_name))


@api_router.post("/match")
def api_match(body: TxBody):
    return _ok(services.post_match(body.transaction_id, agent_name=body.agent_name))


@api_router.post("/cancel")
def api_cancel(body: CancelBody):
    return _ok(services.cancel_document(body.kind, body.key, body.transaction_id))


@ops_router.get("/v1/execution/overview")
def execution_overview():
    txs = store.list_transactions(40)
    from agents.execution.hitl import waiting_rows

    waiting = waiting_rows(txs)
    kpis_row = fetch_one(
        """
        SELECT
          (SELECT COUNT(*) FROM execution_transactions
            WHERE status NOT IN ('SETTLED','CANCELLED')) AS open_transactions,
          (SELECT COUNT(*) FROM execution_transactions
            WHERE status = 'AWAITING_HUMAN' OR stage IN ('AWAITING_HUMAN','PO_PENDING_APPROVAL')) AS hitl,
          (SELECT COUNT(*) FROM execution_transactions WHERE status = 'EXCEPTION' OR stage = 'EXCEPTION') AS exceptions,
          (SELECT COUNT(*) FROM execution_transactions WHERE status = 'SETTLED' OR stage = 'SETTLED') AS settled,
          (SELECT COUNT(*) FROM channel_messages WHERE read_at IS NULL AND direction = 'IN') AS unread_inbox
        """
    )
    kpis = _clean(kpis_row) or {}
    kpis["hitl"] = max(int(kpis.get("hitl") or 0), len(waiting))
    inbox = [_clean(r) for r in fetch_all(
        """
        SELECT message_id, transaction_id, direction, channel, counterparty, subject,
               read_at, created_at, parsed
        FROM channel_messages
        ORDER BY created_at DESC
        LIMIT 40
        """
    )]
    return {
        "kpis": kpis,
        "transactions": txs,
        "waiting": waiting,
        "hitl_requests": waiting,
        "inbox": inbox,
        "memory": store.list_memory(),
        "site": {"name": "System of Execution", "plant": "WH-FR-01"},
        "playbooks": [
            {
                "id": "golden_p2p",
                "title": "PR-1001 golden path",
                "persona": "Operator",
                "agents": "Sourcing → Procurement → Logistics → Finance",
                "trigger": "PR-1001 MAT-11 × 20 for WH-FR-01, need-by +14d.",
                "steps": [
                    "Open Team, then Jobs, and select PR-1001.",
                    "Run the job. Sourcing RFQs three vendors; the +12% bid is rejected in policy.",
                    "Procurement issues PO-1001 (amount is under the approval threshold).",
                    "Logistics books Speedy Express, consumes EDI 214, posts GR.",
                    "Finance captures invoices and posts the four-way match → SETTLED.",
                ],
                "ask": "What stage is PR-1001 at, and which quotes are on file?",
                "transaction_id": "aaaaaaaa-bbbb-cccc-dddd-000000000001",
            },
            {
                "id": "delay_exception",
                "title": "PR-1002 supplier delay",
                "persona": "Operator",
                "agents": "Orchestrator → Logistics (rebook if within slip)",
                "trigger": "Delay email on PO-1002, 3 days (inside 5-day slip).",
                "steps": [
                    "Open Needs you and select PR-1002.",
                    "Run recovery. Logistics should rebook without HITL.",
                    "A 6+ day slip would hold for a human instead.",
                ],
                "ask": "What unread channel messages sit on PR-1002?",
                "transaction_id": "aaaaaaaa-bbbb-cccc-dddd-000000000002",
            },
            {
                "id": "hitl_delay",
                "title": "PR-1003 delay beyond policy (HITL)",
                "persona": "Supervisor",
                "agents": "Orchestrator → Logistics (hold)",
                "trigger": "Delay email on PO-1003, 14 days (policy slip is 5).",
                "steps": [
                    "Open Needs you and select PR-1003.",
                    "Run recovery. Logistics must stop at AWAITING_HUMAN — do not rebook.",
                    "Approve exception & rebook (or Cancel PO). Copilot on this job can confirm with yes.",
                ],
                "ask": "Why is PR-1003 waiting on a human?",
                "transaction_id": "aaaaaaaa-bbbb-cccc-dddd-000000000003",
            },
        ],
    }


@ops_router.get("/v1/execution/transactions/{transaction_id}")
def execution_detail(transaction_id: str):
    try:
        snap = store.get_snapshot(transaction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runs = [_clean(r) for r in fetch_all(
        """
        SELECT run_id, agent_name, status, confidence, result, llm_calls, tool_calls, started_at, ended_at
        FROM agent_runs
        WHERE transaction_id = %s
        ORDER BY started_at
        """,
        (transaction_id,),
    )]
    actions = [_clean(r) for r in fetch_all(
        """
        SELECT action_id, agent_name, action_type, payload, inverse, status, created_at
        FROM actions
        WHERE transaction_id = %s
        ORDER BY created_at
        """,
        (transaction_id,),
    )]
    snap["agent_runs"] = runs
    snap["actions"] = actions
    from agents.execution.hitl import open_request_for

    snap["hitl_request"] = open_request_for(transaction_id=transaction_id)
    return snap


@ops_router.post("/v1/execution/transactions/{transaction_id}/run")
async def run_tx(transaction_id: str, x_trace_id: str | None = Header(default=None)):
    try:
        store.get_snapshot(transaction_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await execute_transaction(transaction_id, trace_id=x_trace_id)


@ops_router.post("/v1/execution/transactions/{transaction_id}/decide")
async def decide_tx(transaction_id: str, body: DecideBody | None = None):
    payload = body or DecideBody()
    decided = services.apply_human_decision(
        transaction_id,
        payload.action,
        option=payload.option,
        note=payload.note,
        request_id=payload.request_id,
    )
    if decided.get("error"):
        raise HTTPException(status_code=400, detail=decided.get("detail") or decided)
    resume_id = decided.get("transaction_id") or transaction_id
    if payload.wait and decided.get("resumed") and resume_id:
        try:
            store.get_snapshot(resume_id)
        except KeyError:
            return decided
        result = await execute_transaction(resume_id)
        result["decision"] = decided
        return result
    return decided


@ops_router.post("/v1/execution/hitl/{request_id}/decide")
async def decide_hitl_request(request_id: str, body: DecideBody | None = None):
    return await decide_tx(request_id, body)


@ops_router.get("/v1/mcp/tools")
def layer2_mcp_tools():
    from agents.execution.hitl_mcp import list_tools

    return {"tools": list_tools()}


class McpCallIn(BaseModel):
    name: str
    arguments: dict = {}


@ops_router.post("/v1/mcp/tools/call")
def layer2_mcp_call(body: McpCallIn):
    from agents.execution.hitl_mcp import call_tool

    try:
        result = call_tool(body.name, body.arguments)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result.get("detail") or result)
    return {"result": result}


@api_router.post("/hitl/request")
def api_hitl_request(body: HitlCreateBody):
    from agents.execution.hitl import create_hitl_request

    return _ok(create_hitl_request(
        question=body.question,
        transaction_id=body.transaction_id,
        agent_name=body.agent_name,
        step=body.step,
        options=body.options,
        recommended_option=body.recommended_option,
        rationale=body.rationale,
        confidence=body.confidence,
        workflow=body.workflow,
    ))


@ops_router.post("/v1/execution/transactions/{transaction_id}/approve")
async def approve_tx(transaction_id: str, wait: bool = True):
    """Back-compat: PO pending → issue PO; delay/match hold → approve exception."""
    return await decide_tx(transaction_id, DecideBody(action="approve_exception", wait=wait))


@ops_router.get("/v1/execution/memory")
def execution_memory():
    return store.list_memory()


_TEAM = [
    {
        "id": "Orchestrator",
        "kind": "company",
        "title": "Orchestrator",
        "job": "Stage machine only. Picks the next specialist or stop.",
        "must_not": "Never RFQs, books freight, or posts AP.",
        "owns_when": "Always: decides who goes next on the selected job.",
        "agent_name": None,
    },
    {
        "id": "Sourcing_Agent",
        "kind": "company",
        "title": "Sourcing",
        "job": "Qualify vendors, send RFQs, normalize bids.",
        "must_not": "Cannot issue a PO or pay anyone.",
        "owns_when": "INTENT / SOURCING until at least two quotes are on file.",
        "agent_name": "Sourcing_Agent",
    },
    {
        "id": "Procurement_Agent",
        "kind": "company",
        "title": "Procurement",
        "job": "Turn a legal quote into a purchase order.",
        "must_not": "Cannot book freight or match invoices.",
        "owns_when": "Quotes exist and no PO yet (or PO pending approval).",
        "agent_name": "Procurement_Agent",
    },
    {
        "id": "Logistics_Agent",
        "kind": "company",
        "title": "Logistics",
        "job": "Book the contracted carrier, tracking, goods receipt, rebook delays.",
        "must_not": "Cannot change PO price or post accounts payable.",
        "owns_when": "PO issued until GR is posted.",
        "agent_name": "Logistics_Agent",
    },
    {
        "id": "Finance_Agent",
        "kind": "company",
        "title": "Finance",
        "job": "Capture invoices and four-way match, then settle.",
        "must_not": "Cannot create POs.",
        "owns_when": "Goods received until SETTLED.",
        "agent_name": "Finance_Agent",
    },
    {
        "id": "VEND-A",
        "kind": "external",
        "title": "Vendor A",
        "job": "Standalone supplier. Bids ~10.20, can invoice and delay.",
        "must_not": "Cannot write ERP PO / GR / match.",
        "owns_when": "Answers RFQs and invoice requests on AgentMail.",
        "agent_name": None,
        "counterparty": "VEND-A",
    },
    {
        "id": "VEND-C",
        "kind": "external",
        "title": "Vendor C",
        "job": "Aggressive bidder ~11.20 (outside ±5% policy).",
        "must_not": "Cannot write ERP. Bid is stored but cannot win.",
        "owns_when": "Answers RFQs on AgentMail.",
        "agent_name": None,
        "counterparty": "VEND-C",
    },
    {
        "id": "VEND-B",
        "kind": "fixture",
        "title": "Vendor B",
        "job": "Third quote only. No AgentMail inbox (three-inbox cap).",
        "must_not": "No live mailbox. Fixture bid only.",
        "owns_when": "Injected when RFQs go out.",
        "agent_name": None,
        "counterparty": "VEND-B",
    },
]


@ops_router.get("/v1/execution/team")
def execution_team():
    from agents.execution.agentmail import agentmail_ready

    last_runs = {
        r["agent_name"]: r
        for r in fetch_all(
            """
            SELECT DISTINCT ON (agent_name)
                   agent_name, status, confidence, result, started_at, ended_at, transaction_id
              FROM agent_runs
             WHERE agent_name IS NOT NULL
             ORDER BY agent_name, started_at DESC
            """
        )
    }
    unread_by = {}
    for row in fetch_all(
        """
        SELECT COALESCE(counterparty, 'unknown') AS counterparty, COUNT(*) AS n
          FROM channel_messages
         WHERE read_at IS NULL AND direction = 'IN'
         GROUP BY 1
        """
    ):
        unread_by[row["counterparty"]] = row["n"]
    live = agentmail_ready()
    members = []
    for spec in _TEAM:
        item = dict(spec)
        name = spec.get("agent_name")
        run = last_runs.get(name) if name else None
        if run:
            result = run.get("result") or {}
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:  # noqa: BLE001
                    result = {}
            item["last_run"] = {
                "status": run.get("status"),
                "confidence": run.get("confidence"),
                "summary": (result or {}).get("summary") or "",
                "verified": (result or {}).get("verified"),
                "findings": (result or {}).get("findings") or [],
                "actions_taken": (result or {}).get("actions_taken") or [],
                "recommendation": (result or {}).get("recommendation") or "",
                "transaction_id": str(run.get("transaction_id") or ""),
                "ended_at": run.get("ended_at"),
            }
        else:
            item["last_run"] = None
        cp = spec.get("counterparty")
        item["unread"] = unread_by.get(cp, 0) if cp else 0
        if spec["kind"] == "external":
            item["mail"] = "live" if live else "fixture"
        elif spec["kind"] == "fixture":
            item["mail"] = "fixture"
        else:
            item["mail"] = None
        members.append(_clean(item))
    return {
        "mail_live": live,
        "policy": {
            "price_deviation_pct": 5,
            "date_slip_days": 5,
            "approval_amount_usd": 5000,
        },
        "members": members,
    }
