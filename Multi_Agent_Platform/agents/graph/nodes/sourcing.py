"""Sourcing agent — qualify vendors, issue RFQs, normalize bids. Must not issue PO or pay."""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from agents.config.db import get_conn
from agents.contracts import AgentResult, AgentStatus
from agents.execution import policy, services, store
from agents.execution.loop import pack_turn
from agents.graph.state import LogisticsState
from agents.tools.knowledge_tools import knowledge_context, similar_incident_context

NAME = "Sourcing_Agent"


async def sourcing_node(state: LogisticsState, config: RunnableConfig):
    tx_id = state["transaction_id"]
    snap = store.get_snapshot(tx_id)

    vendors = snap["vendors"]
    quotes = snap["quotes"]
    findings = [
        f"PR {(snap['pr'] or {}).get('pr_id')} material {(snap['pr'] or {}).get('material_id')} "
        f"qty {(snap['pr'] or {}).get('quantity')}",
        f"{len(vendors)} active vendors, {len(quotes)} quotes on file, {len(snap['unread'])} unread inbound",
    ]

    if not vendors:
        result = AgentResult(
            status=AgentStatus.FAILED,
            summary="No active vendors to RFQ.",
            findings=findings,
            confidence=0.2,
            verified=False,
        )
        return pack_turn(NAME, result)

    issued = {"rfqs": [], "quotes": quotes}
    if len(quotes) < 2:
        issued = await asyncio.to_thread(services.issue_rfqs, tx_id, agent_name=NAME)
        if issued.get("error"):
            result = AgentResult(
                status=AgentStatus.FAILED,
                summary=str(issued.get("detail") or issued),
                findings=findings,
                confidence=0.2,
                verified=False,
            )
            return pack_turn(NAME, result)
        quotes = issued.get("quotes") or store.get_snapshot(tx_id)["quotes"]

    last_price = (snap["material"] or {}).get("last_price") or 0
    max_pct = (snap["policy"] or {}).get("price_deviation_pct")
    legal = policy.legal_quotes(quotes, last_price, max_pct)
    legal_vendors = {q["vendor_id"] for q in legal}
    rejected = [q["vendor_id"] for q in quotes if q["vendor_id"] not in legal_vendors]

    verified = len(quotes) >= 2
    if not verified:
        result = AgentResult(
            status=AgentStatus.PARTIAL,
            summary="RFQs sent; waiting for at least two bids.",
            findings=findings,
            actions_taken=[f"issued {len(issued.get('rfqs') or [])} RFQs"],
            confidence=0.6,
            verified=False,
            recommendation="Wait for vendor replies.",
        )
        return pack_turn(NAME, result, tool_calls=1)

    with get_conn() as conn:
        for quote in quotes:
            store.write_supplier_memory(
                conn,
                quote["vendor_id"],
                tx_id,
                0.0,
                "bid ingested" if quote["vendor_id"] in legal_vendors else "bid outside price policy",
            )

    winner = policy.pick_winner(legal)
    findings.append(
        f"{len(quotes)} quotes; {len(legal)} inside ±{max_pct}% of last price {last_price}; "
        f"rejected {rejected}"
    )
    kg = knowledge_context(
        f"SLA and contract for {(winner or {}).get('vendor_id') or 'VEND-A'} MAT-11 WH-FR-01"
    )
    if kg:
        findings.append(f"GraphRAG: {kg[:500]}")
    past = similar_incident_context("VEND-C") or similar_incident_context((winner or {}).get("vendor_id") or "VEND-A")
    if past:
        findings.append(f"Prior incidents: {past[:400]}")
    result = AgentResult(
        status=AgentStatus.SUCCESS,
        summary=(
            f"Normalized {len(quotes)} bids. Legal winner "
            f"{(winner or {}).get('vendor_id')} @ {(winner or {}).get('unit_price')}."
        ),
        findings=findings,
        actions_taken=[f"RFQs {[r.get('rfq_id') for r in issued.get('rfqs') or []]}"],
        evidence=[f"quotes:{[q.get('quote_id') for q in quotes]}"],
        confidence=0.95,
        verified=True,
        recommendation="Procurement should create the PO from the legal winner.",
    )
    return pack_turn(NAME, result, tool_calls=len(issued.get("rfqs") or []))
