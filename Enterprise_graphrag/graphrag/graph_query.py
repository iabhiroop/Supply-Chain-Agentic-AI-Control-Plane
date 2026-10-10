"""Templated graph queries only — no free Cypher from agents."""

from __future__ import annotations

from typing import Any

from graphrag.graph_store import get_entity, traverse
from graphrag.neo4j_client import client as neo4j


TEMPLATES = ("neighbors", "risk_if_slip", "path", "entity", "similar_incidents", "open_pos_at_plant")


def run_template(template: str, *, key: str | None = None, src: str | None = None, dst: str | None = None, hops: int = 2) -> dict[str, Any]:
    template = (template or "").strip().lower()
    if template not in TEMPLATES:
        return {"error": True, "detail": f"Unknown template. Allowed: {TEMPLATES}"}
    if template == "entity":
        ent = get_entity(key or "")
        return {"template": template, "entity": ent}
    if template == "neighbors":
        paths = traverse([key or ""], hops=hops)
        return {"template": template, "key": key, "hops": hops, "paths": _fmt(paths)}
    if template == "risk_if_slip":
        paths = traverse([key or ""], hops=max(3, hops))
        orders = sorted({p["dst_key"] for p in paths if p.get("dst_label") == "Order"} | {p["src_key"] for p in paths if p.get("src_label") == "Order"})
        sites = sorted({p["dst_key"] for p in paths if p.get("dst_label") in {"Site", "Warehouse"}} | {p["src_key"] for p in paths if p.get("src_label") in {"Site", "Warehouse"}})
        materials = sorted({p["dst_key"] for p in paths if p.get("dst_label") == "Material"} | {p["src_key"] for p in paths if p.get("src_label") == "Material"})
        return {
            "template": template,
            "supplier": key,
            "exposed_orders": orders,
            "exposed_sites": sites,
            "materials": materials,
            "paths": _fmt(paths),
        }
    if template == "path":
        neighbourhood = traverse([src or "", dst or ""], hops=hops)
        return {"template": template, "src": src, "dst": dst, "paths": _fmt(neighbourhood)}
    if template == "similar_incidents":
        rows = neo4j.similar_incidents(key or "")
        if rows is None:
            rows = _similar_from_paths(key or "")
        return {"template": template, "key": key, "incidents": rows}
    if template == "open_pos_at_plant":
        paths = traverse([key or ""], hops=max(3, hops))
        orders = sorted(
            {
                p["dst_key"]
                for p in paths
                if p.get("dst_label") == "Order" or str(p.get("dst_key") or "").startswith("PO-")
            }
            | {
                p["src_key"]
                for p in paths
                if p.get("src_label") == "Order" or str(p.get("src_key") or "").startswith("PO-")
            }
        )
        return {
            "template": template,
            "plant": key,
            "open_pos": orders,
            "paths": _fmt(paths),
        }
    return {"error": True, "detail": "unhandled"}


def _fmt(paths: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    seen = set()
    for p in paths:
        if not p.get("src_key") or not p.get("dst_key"):
            continue
        line = f"{p.get('src_label')}:{p.get('src_key')} -[{p.get('rel_type')}]-> {p.get('dst_label')}:{p.get('dst_key')}"
        if line in seen:
            continue
        seen.add(line)
        out.append({"path": line, "rel": p["rel_type"], "src": p["src_key"], "dst": p["dst_key"]})
    return out[:40]


def _similar_from_paths(key: str) -> list[dict]:
    paths = traverse([key], hops=2)
    incidents = {}
    for p in paths:
        for lab, k, name in (
            (p.get("src_label"), p.get("src_key"), p.get("src_name")),
            (p.get("dst_label"), p.get("dst_key"), p.get("dst_name")),
        ):
            if lab == "Incident" and k:
                incidents[k] = {"incident_key": k, "incident": name, "via": p.get("rel_type")}
    return list(incidents.values())[:20]
