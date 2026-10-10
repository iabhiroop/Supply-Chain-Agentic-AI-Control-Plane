"""Ontology-constrained LLM extract → graph MERGE. Regex MENTIONS stay the baseline."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from graphrag.graph_store import merge_edge, merge_node
from graphrag.llm import chat
from graphrag.ontology import NODE_TYPES, REL_TYPES, extract_entity_keys, label_for_key

SKIP_LABELS = {"Document", "Chunk", "Community"}
SKIP_RELS = {"MENTIONS", "DERIVED_FROM"}
MAX_CHARS = 6000
WINDOW_OVERLAP = 400
MAX_WINDOWS = 8


def llm_ingest_enabled() -> bool:
    flag = (os.getenv("GRAPHRAG_LLM_INGEST") or "true").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if os.getenv("GRAPHRAG_EXTRACT_ONLY", "").lower() == "true":
        return False
    return True


def _system_prompt() -> str:
    nodes = [n for n in NODE_TYPES if n not in SKIP_LABELS]
    rels = [r for r in REL_TYPES if r not in SKIP_RELS]
    return (
        "Extract supply-chain entities and relationships for a knowledge graph. "
        "Use ONLY these node labels: " + ", ".join(nodes) + ". "
        "Use ONLY these relationship types: " + ", ".join(rels) + ". "
        "Prefer business keys already in the text (VEND-ABC, PO-1842, MAT-SEMI-1, PL-IN-CHN). "
        "Do not invent keys, plants, POs, or vendors that are not in the document. "
        "Reply with JSON only: "
        '{"entities":[{"key":"VEND-ABC","label":"Supplier","name":"..."}],'
        '"relations":[{"src":"VEND-ABC","rel":"SUPPLIES","dst":"MAT-SEMI-1"}]}'
    )


def parse_extraction(raw: str | None) -> dict[str, Any]:
    if not raw or not str(raw).strip():
        return {"entities": [], "relations": []}
    text = str(raw).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"entities": [], "relations": []}
    if not isinstance(payload, dict):
        return {"entities": [], "relations": []}
    return {
        "entities": payload.get("entities") or [],
        "relations": payload.get("relations") or [],
    }


def _norm_key(key: str) -> str:
    k = (key or "").strip()
    if re.match(r"^(vend|mat|pr|po|wh|pl)-", k, re.I):
        return k.upper()
    return k


def _in_text(value: str, blob: str) -> bool:
    if not value:
        return False
    low = blob.lower()
    if value.lower() in low:
        return True
    compact_v = re.sub(r"[^a-z0-9]", "", value.lower())
    compact_t = re.sub(r"[^a-z0-9]", "", low)
    return len(compact_v) >= 4 and compact_v in compact_t


def sanitize(payload: dict[str, Any], source_text: str) -> dict[str, list]:
    """Drop off-ontology types and anything not grounded in the source text."""
    entities = []
    seen: set[str] = set()
    for raw in payload.get("entities") or []:
        if not isinstance(raw, dict):
            continue
        key = _norm_key(str(raw.get("key") or ""))
        label = str(raw.get("label") or "").strip()
        name = str(raw.get("name") or key).strip()
        if not key or label not in NODE_TYPES or label in SKIP_LABELS:
            continue
        if not (_in_text(key, source_text) or _in_text(name, source_text)):
            continue
        if key in seen:
            continue
        seen.add(key)
        entities.append({"key": key, "label": label, "name": name})
    allowed = {e["key"] for e in entities} | set(extract_entity_keys(source_text))
    relations = []
    for raw in payload.get("relations") or []:
        if not isinstance(raw, dict):
            continue
        src = _norm_key(str(raw.get("src") or raw.get("source") or ""))
        dst = _norm_key(str(raw.get("dst") or raw.get("target") or ""))
        rel = str(raw.get("rel") or raw.get("type") or "").strip().upper()
        if rel not in REL_TYPES or rel in SKIP_RELS:
            continue
        if src not in allowed or dst not in allowed:
            continue
        if src == dst:
            continue
        item = {"src": src, "rel": rel, "dst": dst}
        if raw.get("confidence") is not None:
            try:
                item["confidence"] = float(raw["confidence"])
            except (TypeError, ValueError):
                pass
        relations.append(item)
    return {"entities": entities, "relations": relations}


def iter_windows(text: str, *, max_chars: int = MAX_CHARS, overlap: int = WINDOW_OVERLAP, cap: int = MAX_WINDOWS) -> list[str]:
    """Split a long document so later pages are interpreted, not only the first 6k."""
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= max_chars:
        return [body]
    step = max(1, max_chars - overlap)
    windows: list[str] = []
    start = 0
    while start < len(body) and len(windows) < cap:
        windows.append(body[start : start + max_chars])
        if start + max_chars >= len(body):
            break
        start += step
    return windows


def extract_from_text(text: str, *, chat_fn=None) -> dict[str, Any]:
    body = (text or "")[:MAX_CHARS]
    if not body.strip():
        return {"entities": [], "relations": [], "skipped": True, "reason": "empty", "windows": 0}
    if not llm_ingest_enabled():
        return {"entities": [], "relations": [], "skipped": True, "reason": "llm_ingest_disabled", "windows": 0}
    fn = chat_fn or chat
    raw = fn(_system_prompt(), f"Document:\n{body}")
    if not raw:
        return {"entities": [], "relations": [], "skipped": True, "reason": "gateway_unavailable", "windows": 1}
    cleaned = sanitize(parse_extraction(raw), body)
    cleaned["skipped"] = False
    cleaned["reason"] = ""
    cleaned["windows"] = 1
    return cleaned


def apply_extraction(
    payload: dict[str, Any],
    *,
    document_id: str,
    doc_node_id: str,
    source_text: str,
    extractor: str = "llm",
) -> dict[str, int]:
    """MERGE extracted entities/relations. Also link Document MENTIONS / DERIVED_FROM."""
    cleaned = sanitize(payload, source_text)
    ids: dict[str, str] = {}
    for ent in cleaned["entities"]:
        nid = merge_node(ent["label"], ent["key"], ent["name"], {"extractor": extractor})
        ids[ent["key"]] = nid
        merge_edge(
            "MENTIONS",
            doc_node_id,
            nid,
            provenance={"document_id": document_id, "extractor": extractor},
        )
        merge_edge(
            "DERIVED_FROM",
            nid,
            doc_node_id,
            provenance={"document_id": document_id, "extractor": extractor},
        )
    for key in extract_entity_keys(source_text):
        if key not in ids:
            ids[key] = merge_node(label_for_key(key), key, key)
    wrote = 0
    for rel in cleaned["relations"]:
        src = ids.get(rel["src"])
        dst = ids.get(rel["dst"])
        if not src or not dst:
            continue
        merge_edge(
            rel["rel"],
            src,
            dst,
            confidence=float(rel.get("confidence") or 1.0),
            provenance={"document_id": document_id, "extractor": extractor},
        )
        wrote += 1
    return {"entities": len(cleaned["entities"]), "relations": wrote}


def extract_and_apply(text: str, *, document_id: str, doc_node_id: str, chat_fn=None) -> dict[str, Any]:
    windows = iter_windows(text)
    if not windows:
        return {"entities": 0, "relations": 0, "skipped": True, "reason": "empty", "windows": 0}
    if not llm_ingest_enabled():
        return {"entities": 0, "relations": 0, "skipped": True, "reason": "llm_ingest_disabled", "windows": 0}
    total_ent = 0
    total_rel = 0
    last_skip = ""
    any_ok = False
    for win in windows:
        extracted = extract_from_text(win, chat_fn=chat_fn)
        if extracted.get("skipped"):
            last_skip = str(extracted.get("reason") or last_skip)
            continue
        counts = apply_extraction(
            extracted,
            document_id=document_id,
            doc_node_id=doc_node_id,
            source_text=win,
        )
        total_ent += int(counts.get("entities") or 0)
        total_rel += int(counts.get("relations") or 0)
        any_ok = True
    if not any_ok:
        return {
            "entities": 0,
            "relations": 0,
            "skipped": True,
            "reason": last_skip or "gateway_unavailable",
            "windows": len(windows),
        }
    return {
        "entities": total_ent,
        "relations": total_rel,
        "skipped": False,
        "reason": "",
        "windows": len(windows),
    }
