"""Cited answers through the AI Gateway. Abstain when context is empty."""

from __future__ import annotations

import re
from typing import Any

from graphrag.llm import chat
from graphrag.retrieve import retrieve


def _extractive(query: str, retrieved: dict[str, Any]) -> str:
    passages = retrieved.get("passages") or []
    paths = retrieved.get("graph_paths") or []
    lines = []
    if paths and retrieved.get("intent") in {"multihop", "aggregation"}:
        lines.append("Graph neighbourhood (traversal):")
        for p in paths[:8]:
            lines.append(f"- {p['path']}")
    for p in passages[:4]:
        cite = p.get("citation") or p.get("title")
        snippet = (p.get("text") or "").replace("\n", " ")[:420]
        lines.append(f"{snippet} [{cite}]")
    if retrieved.get("communities"):
        for c in retrieved["communities"][:2]:
            lines.append(f"{c.get('summary')} [community:{c.get('community_id')}]")
    if not lines:
        return "I don't have information on that."
    return " ".join(lines) if not paths else "\n".join(lines)


def _grounded(text: str, retrieved: dict[str, Any]) -> tuple[str, float, bool]:
    passages = retrieved.get("passages") or []
    cites = [p.get("citation") for p in passages if p.get("citation")]
    if not passages and not retrieved.get("graph_paths"):
        return "I don't have information on that.", 0.0, True
    blob = " ".join((p.get("text") or "") for p in passages).lower()
    blob += " " + " ".join(p.get("path") or "" for p in (retrieved.get("graph_paths") or []))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]
    kept = []
    for sent in sentences:
        if sent.lower().startswith("i don't have"):
            kept.append(sent)
            continue
        tokens = [t for t in re.findall(r"[a-z0-9-]{4,}", sent.lower()) if t not in {"that", "this", "with", "from", "have"}]
        overlap = sum(1 for t in tokens if t in blob) / max(1, len(tokens))
        if overlap >= 0.25 or any(c and c in sent for c in cites) or "[" in sent:
            if "[" not in sent and cites:
                sent = f"{sent.rstrip('.')} [{cites[0]}]."
            kept.append(sent)
    if not kept:
        return "I don't have information on that.", 0.15, True
    coverage = min(1.0, len(kept) / max(1, len(sentences)))
    abstained = all("don't have information" in s.lower() for s in kept)
    return " ".join(kept), round(0.35 + 0.5 * coverage, 2), abstained


_GENERIC = {
    "which", "what", "when", "where", "orders", "order", "plants", "plant", "exposed",
    "slips", "weeks", "standard", "lead", "time", "quote", "price", "policy", "legal",
    "open", "supplier", "vendors", "customer", "freight", "rate", "warehouse",
    "contractual", "overall", "themes", "inbound", "dairy", "information",
}


def _unsupported_entity(query: str, retrieved: dict[str, Any]) -> bool:
    blob = " ".join(
        [p.get("text") or "" for p in retrieved.get("passages") or []]
        + [p.get("path") or "" for p in retrieved.get("graph_paths") or []]
    ).lower()
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", query) if t.lower() not in _GENERIC]
    rare = [t for t in tokens if t not in blob]
    return bool(rare) and len(rare) >= max(1, len(tokens) // 2)


def answer(query: str, *, tenant_id: str | None = None, k: int = 8) -> dict[str, Any]:
    retrieved = retrieve(query, k=k, tenant_id=tenant_id)
    passages = retrieved.get("passages") or []
    paths = retrieved.get("graph_paths") or []
    if _unsupported_entity(query, retrieved):
        return {
            "query": query,
            "answer": "I don't have information on that.",
            "abstained": True,
            "confidence": 0.0,
            "coverage": 0.0,
            "citations": [],
            "retrieval": retrieved,
            "planned_query": retrieved.get("planned_query"),
            "graph_template": retrieved.get("graph_template"),
        }
    if not passages and not paths and not retrieved.get("communities"):
        return {
            "query": query,
            "answer": "I don't have information on that.",
            "abstained": True,
            "confidence": 0.0,
            "coverage": 0.0,
            "citations": [],
            "retrieval": retrieved,
            "planned_query": retrieved.get("planned_query"),
            "graph_template": retrieved.get("graph_template"),
        }

    ctx_passages = "\n\n".join(
        f"[{p.get('citation')}]\n{p.get('text')}" for p in passages
    )
    ctx_paths = "\n".join(p.get("path") or "" for p in paths[:15])
    system = (
        "You answer supply-chain questions using ONLY the provided passages and graph paths. "
        "Put a citation in square brackets after every claim, using the exact [title#anchor] labels. "
        "If the context does not support an answer, reply exactly: I don't have information on that. "
        "Never invent orders, vendors, or SLAs."
    )
    user = f"Question: {query}\n\nGraph paths:\n{ctx_paths}\n\nPassages:\n{ctx_passages}"
    raw = chat(system, user) or _extractive(query, retrieved)
    text, confidence, abstained = _grounded(raw, retrieved)
    citations = [{"citation": p.get("citation"), "document_id": p.get("document_id"), "source_uri": p.get("source_uri")} for p in passages]
    return {
        "query": query,
        "answer": text,
        "abstained": abstained,
        "confidence": confidence,
        "coverage": 0.0 if abstained else min(1.0, len(passages) / 4),
        "citations": citations,
        "retrieval": retrieved,
        "planned_query": retrieved.get("planned_query"),
        "graph_template": retrieved.get("graph_template"),
    }
