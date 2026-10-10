"""Natural language → GraphRAG templates. Free Cypher is never produced."""

from __future__ import annotations

import re
from typing import Any

TEMPLATES = (
    "neighbors",
    "risk_if_slip",
    "path",
    "entity",
    "similar_incidents",
    "open_pos_at_plant",
)


def plan_query(query: str) -> dict[str, Any]:
    from graphrag.retrieve import classify_intent, extract_anchors

    intent = classify_intent(query)
    anchors = extract_anchors(query)
    q = (query or "").lower()
    hops = 3 if any(a in {"VEND-ABC", "MAT-SEMI-1"} for a in anchors) else 2

    vendors = [a for a in anchors if a.startswith("VEND-")]
    plants = [a for a in anchors if a.startswith("PL-") or a.startswith("WH-")]
    materials = [a for a in anchors if a.startswith("MAT-")]
    orders = [a for a in anchors if a.startswith("PO-") or a.startswith("PR-")]

    template = "neighbors"
    key = anchors[0] if anchors else None
    src = None
    dst = None

    if any(w in q for w in ("similar incident", "similar incidents", "prior incident", "past incident")):
        template = "similar_incidents"
        key = vendors[0] if vendors else (anchors[0] if anchors else None)
    elif re.search(r"\bpath\b|\bfrom\b.+\bto\b", q) and len(anchors) >= 2:
        template = "path"
        src = vendors[0] if vendors else anchors[0]
        dst = orders[0] if orders else (plants[0] if plants else next((a for a in anchors if a != src), anchors[-1]))
        key = None
    elif ("open po" in q or "open purchase" in q or "pos at" in q) and plants:
        template = "open_pos_at_plant"
        key = plants[0]
    elif intent in {"multihop", "aggregation"} or any(
        w in q for w in ("if ", "exposed", "risk", "slip", "shutdown", "shut down", "depends")
    ):
        template = "risk_if_slip"
        key = vendors[0] if vendors else (anchors[0] if anchors else "VEND-ABC")
    elif "neighbor" in q or "connected to" in q:
        template = "neighbors"
        key = anchors[0] if anchors else key
    elif intent == "local" and len(anchors) == 1:
        template = "entity"
        key = anchors[0]
    elif orders and not vendors:
        template = "entity"
        key = orders[0]
    elif materials and not vendors:
        template = "neighbors"
        key = materials[0]

    return {
        "query": query,
        "intent": intent,
        "template": template,
        "key": key,
        "src": src,
        "dst": dst,
        "hops": hops,
        "anchors": anchors,
        "cypher": None,
        "note": "Templates only; free Cypher is disallowed.",
    }
