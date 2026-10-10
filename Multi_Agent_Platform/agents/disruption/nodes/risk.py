"""Supplier Risk — news + GraphRAG location. Does not invent duration."""

from __future__ import annotations

import asyncio
import json

from langchain_core.runnables import RunnableConfig

from agents.contracts import AgentResult, AgentStatus
from agents.disruption.state import DisruptionState
from agents.execution.loop import pack_turn
from agents.memory.store import already_applied, last_experience
from agents.tools import twin_tools
from agents.tools.knowledge_tools import knowledge_context

NAME = "Supplier_Risk_Agent"


def _payload(row: dict) -> dict:
    raw = row.get("payload") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


async def risk_node(state: DisruptionState, config: RunnableConfig):
    vendor_id = state.get("vendor_id") or "VEND-ABC"
    news = await asyncio.to_thread(twin_tools.news_signals, vendor_id)
    tools = 1
    findings = []
    evidence = []
    duration = None
    country = None
    headline = None
    if news:
        payload = _payload(news[0])
        duration = payload.get("duration_days")
        country = payload.get("country")
        headline = news[0].get("headline")
        findings.append(headline or f"News signal {news[0].get('signal_id')}")
        if duration is not None:
            findings.append(f"duration_days={duration} (twin news payload, not inferred)")
        if country:
            findings.append(f"country={country}")
        evidence.append(f"twin.news:{news[0].get('signal_id')}")
    else:
        findings.append("No twin news for this vendor.")

    kg = await asyncio.to_thread(
        knowledge_context, f"Which plants if {vendor_id} has a two-week shutdown?"
    )
    if kg:
        findings.append(f"GraphRAG: {kg[:400]}")
        evidence.append("graphrag:/v1/retrieve")
        tools += 1

    prior = last_experience(vendor_id=vendor_id)
    if prior:
        findings.append(
            f"Prior experience [{prior.get('flow')}]: {(prior.get('summary') or '')[:240]}"
        )
        evidence.append(f"experience:{prior.get('experience_id')}")
    applied = already_applied("PO-1842")
    if applied:
        findings.append(
            f"PO-1842 already had disruption_apply ({applied.get('summary')}); do not blindly repeat."
        )

    ok = duration is not None
    result = AgentResult(
        status=AgentStatus.SUCCESS if ok else AgentStatus.PARTIAL,
        summary=(
            f"{vendor_id} high-risk event: {duration}-day shutdown"
            + (f" in {country}" if country else "")
            + "."
            if ok
            else f"No structured shutdown news for {vendor_id}."
        ),
        findings=findings,
        evidence=evidence,
        confidence=1.0 if ok else 0.4,
        verified=ok,
        recommendation="Inventory should report days of cover on the affected material.",
    )
    facts = {}
    if duration is not None:
        facts["news.duration_days"] = duration
    if country:
        facts["news.country"] = country
    update = pack_turn(NAME, result, tool_calls=tools, facts=facts, workflow="disruption")
    update["halt_reason"] = ""
    return update
