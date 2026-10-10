"""Golden retrieval questions: local, multi-hop, global, abstain."""

from __future__ import annotations

import os
from typing import Any

from graphrag.answer import answer
from graphrag.main import bootstrap
from graphrag.retrieve import retrieve

QUESTIONS = [
    {
        "id": "local_lead",
        "query": "What is the standard lead time for VEND-A on MAT-11?",
        "must": ["5"],
        "intent": "local",
    },
    {
        "id": "multihop_slip",
        "query": "Which open orders and plants are exposed if VEND-A slips two weeks?",
        "must": ["WH-FR-01"],
        "any": ["PO-1001", "PR-1001", "PR-1002", "MAT-11"],
        "intent": "multihop",
        "require_graph": True,
    },
    {
        "id": "global_themes",
        "query": "What are the overall themes in supplier risk for dairy inbound?",
        "any": ["Spain", "VEND-A", "WH-FR-01", "dairy"],
        "intent": "global",
    },
    {
        "id": "abstain",
        "query": "What is the contractual freight rate to the Mars colony warehouse?",
        "abstain": True,
    },
    {
        "id": "vend_c_policy",
        "query": "Is the VEND-C quote of 11.20 legal under price policy?",
        "any": ["5%", "12%", "illegal", "exception"],
    },
    {
        "id": "experience_delay",
        "query": "What worked last time VEND-A delayed?",
        "any": ["rebook", "Speedy Express", "INC-DELAY", "3 days"],
    },
    {
        "id": "abc_shutdown",
        "query": "Which plants and purchase orders are exposed if VEND-ABC has a two-week shutdown?",
        "must": ["PL-IN-CHN", "PO-1842"],
        "any": ["MAT-SEMI-1", "Taiwan", "PL-IN-BLR", "VEND-ABC"],
        "intent": "multihop",
        "require_graph": True,
    },
]


def _blob(retrieved: dict[str, Any], ans: dict[str, Any] | None = None) -> str:
    parts = [p.get("text") or "" for p in retrieved.get("passages") or []]
    parts += [p.get("path") or "" for p in retrieved.get("graph_paths") or []]
    if ans:
        parts.append(ans.get("answer") or "")
    return " ".join(parts)


def run_golden(*, bootstrap_first: bool = True) -> dict[str, Any]:
    os.environ["GRAPHRAG_EXTRACT_ONLY"] = "true"
    if bootstrap_first and os.getenv("GRAPHRAG_GOLDEN_SKIP_BOOTSTRAP", "").lower() != "true":
        bootstrap()
    results = []
    passed = 0
    for q in QUESTIONS:
        retrieved = retrieve(q["query"], k=8)
        ans = answer(q["query"], k=8)
        blob = _blob(retrieved, ans).lower()
        ok = True
        reasons = []
        if q.get("abstain"):
            ok = bool(ans.get("abstained")) or "don't have information" in (ans.get("answer") or "").lower()
            if not ok:
                reasons.append("expected abstention")
        else:
            for token in q.get("must") or []:
                if token.lower() not in blob:
                    ok = False
                    reasons.append(f"missing {token}")
            any_tok = q.get("any") or []
            if any_tok and not any(t.lower() in blob for t in any_tok):
                ok = False
                reasons.append(f"missing any of {any_tok}")
        if q.get("require_graph") and not retrieved.get("graph_paths"):
            ok = False
            reasons.append("no graph paths")
        vec = retrieve(q["query"], k=8, vector_only=True)
        hybrid_advantage = None
        if q.get("require_graph"):
            hybrid_advantage = bool(retrieved.get("graph_paths")) and not vec.get("graph_paths")
            if not hybrid_advantage:
                ok = False
                reasons.append("hybrid did not add graph paths vs vector-only")
        row = {
            "id": q["id"],
            "passed": ok,
            "reasons": reasons,
            "intent": retrieved.get("intent"),
            "strategies": retrieved.get("strategies"),
            "n_passages": len(retrieved.get("passages") or []),
            "n_paths": len(retrieved.get("graph_paths") or []),
            "abstained": ans.get("abstained"),
            "hybrid_advantage": hybrid_advantage,
        }
        results.append(row)
        if ok:
            passed += 1
    return {"passed": passed == len(QUESTIONS), "score": f"{passed}/{len(QUESTIONS)}", "results": results}
