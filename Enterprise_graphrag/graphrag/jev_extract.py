"""Jev graph extract: code finds candidates, Jev judges types and relations.

Jev does not generate JSON graphs. Regex/code proposes keys already in the window;
one System One call per window classifies each key and each compatible pair.
Code then MERGE-s accepted facts into Postgres + Neo4j via graph_store.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from graphrag.extract import SKIP_LABELS, apply_extraction, iter_windows, sanitize
from graphrag.ontology import NODE_TYPES, REL_TYPES, extract_entity_keys, label_for_key

SystemOneFn = Callable[..., Any]

NONE = "none"
MAX_CANDIDATES = 12
MAX_PAIR_QUESTIONS = 40
LABEL_CONFIDENCE_MIN = 0.45
REL_CONFIDENCE_MIN = 0.50
STATED_NOUL_MIN = 0.65

BUSINESS_LABELS = [n for n in NODE_TYPES if n not in SKIP_LABELS]

EXTRA_KEY_RE = re.compile(
    r"\b(C-[A-Z0-9-]+|SLA-[A-Z0-9-]+|INC-[A-Z0-9-]+|DEC-[A-Z0-9-]+)\b",
    re.I,
)

LABEL_CRITERIA: dict[str, str] = {
    "Supplier": "A vendor or supplier identified in the window.",
    "Material": "A part, SKU, or material identified in the window.",
    "Product": "A finished product, not a raw material or plant.",
    "Order": "A purchase order or purchase requisition.",
    "Shipment": "A physical shipment or consignment.",
    "Carrier": "A freight carrier or logistics provider.",
    "Customer": "A sold-to customer.",
    "Warehouse": "A warehouse or DC (WH-* keys).",
    "Site": "A plant, site, or DC (PL-* or WH-* keys).",
    "Contract": "A commercial contract document or contract id.",
    "SLA": "A service-level agreement.",
    "Policy": "A procurement, price, or operating policy.",
    "Incident": "A disruption, delay, quality, or breach event.",
    "Decision": "An explicit decision or playbook action taken.",
    "Person": "A named person, not a company or site.",
    "Region": "A geography such as a country or region name.",
    NONE: "This span is not a real scm-v1 business entity in the window.",
}

REL_CRITERIA: dict[str, str] = {
    "SUPPLIES": "The source vendor sells or provides the destination material.",
    "ALTERNATE_FOR": "The source is a qualified alternate supplier for the destination material.",
    "COMPONENT_OF": "The source material is a component of the destination product or material.",
    "CONTAINS": "The source order includes the destination material or product.",
    "SHIPPED_VIA": "The source order or shipment is booked with the destination carrier.",
    "ORDERED_BY": "The source order is for the destination customer.",
    "STORED_AT": "The source order or material is destined for the destination site or warehouse.",
    "GOVERNED_BY": "The source is commercially governed by the destination contract, SLA, or policy.",
    "BREACHES": "The source incident breaches the destination policy or SLA.",
    "AFFECTED_BY": "The source order, supplier, site, or material is affected by the destination incident.",
    "RESOLVED_BY": "The source incident is resolved by the destination decision.",
    "LOCATED_IN": "The source supplier or site is located in the destination region.",
    "LEARNED_FROM": "The source decision was learned from the destination incident.",
    "USES": "The source plant or warehouse consumes or uses the destination material.",
}

REL_EDGES: list[tuple[str, str, str]] = [
    ("SUPPLIES", "Supplier", "Material"),
    ("SUPPLIES", "Supplier", "Product"),
    ("ALTERNATE_FOR", "Supplier", "Material"),
    ("USES", "Site", "Material"),
    ("USES", "Warehouse", "Material"),
    ("LOCATED_IN", "Supplier", "Region"),
    ("LOCATED_IN", "Site", "Region"),
    ("LOCATED_IN", "Warehouse", "Region"),
    ("ORDERED_BY", "Order", "Customer"),
    ("CONTAINS", "Order", "Material"),
    ("CONTAINS", "Order", "Product"),
    ("STORED_AT", "Order", "Site"),
    ("STORED_AT", "Order", "Warehouse"),
    ("SHIPPED_VIA", "Order", "Carrier"),
    ("SHIPPED_VIA", "Shipment", "Carrier"),
    ("GOVERNED_BY", "Order", "Policy"),
    ("GOVERNED_BY", "Order", "SLA"),
    ("GOVERNED_BY", "Order", "Contract"),
    ("GOVERNED_BY", "Supplier", "Policy"),
    ("GOVERNED_BY", "Supplier", "Contract"),
    ("BREACHES", "Incident", "Policy"),
    ("BREACHES", "Incident", "SLA"),
    ("AFFECTED_BY", "Order", "Incident"),
    ("AFFECTED_BY", "Supplier", "Incident"),
    ("AFFECTED_BY", "Site", "Incident"),
    ("AFFECTED_BY", "Material", "Incident"),
    ("RESOLVED_BY", "Incident", "Decision"),
    ("LEARNED_FROM", "Decision", "Incident"),
    ("COMPONENT_OF", "Material", "Product"),
    ("COMPONENT_OF", "Material", "Material"),
]

_REL_INDEX: dict[tuple[str, str], list[str]] = {}
for _rel, _src, _dst in REL_EDGES:
    _REL_INDEX.setdefault((_src, _dst), [])
    if _rel not in _REL_INDEX[(_src, _dst)]:
        _REL_INDEX[(_src, _dst)].append(_rel)


def jev_ingest_enabled() -> bool:
    if os.getenv("GRAPHRAG_EXTRACT_ONLY", "").lower() == "true":
        return False
    flag = (os.getenv("GRAPHRAG_JEV_INGEST") or "true").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def load_typesafe_env() -> None:
    here = Path(__file__).resolve()
    load_dotenv(here.parents[1] / "JevTest" / ".env")
    load_dotenv()


def _extra_label(key: str) -> str:
    k = (key or "").upper()
    if k.startswith("C-"):
        return "Contract"
    if k.startswith("SLA-"):
        return "SLA"
    if k.startswith("INC-"):
        return "Incident"
    if k.startswith("DEC-"):
        return "Decision"
    return label_for_key(key)


def find_candidates(text: str) -> list[dict[str, str]]:
    """Code-side candidate finder: ontology keys plus a few extra id patterns."""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    keys = extract_entity_keys(text)
    if re.search(r"speedy express", text, re.I):
        keys.append("Speedy Express")
    keys.extend(m.upper() for m in EXTRA_KEY_RE.findall(text or ""))
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        label = "Carrier" if key == "Speedy Express" else _extra_label(key)
        out.append({"key": key, "default_label": label, "name": key})
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def rel_options(src_label: str, dst_label: str) -> list[str]:
    return list(_REL_INDEX.get((src_label, dst_label), []))


def _qid(*parts: str) -> str:
    return "__".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_") for p in parts)


def _choice_answer(response: Any, qid: str) -> Any | None:
    answers = getattr(response, "answers", None)
    if isinstance(answers, dict) and qid in answers:
        return answers[qid]
    choices = getattr(response, "choices", None)
    if isinstance(choices, dict):
        return choices.get(qid)
    return None


def _noul_answer(response: Any, qid: str) -> Any | None:
    answers = getattr(response, "answers", None)
    if isinstance(answers, dict) and qid in answers:
        return answers[qid]
    nouls = getattr(response, "nouls", None)
    if isinstance(nouls, dict):
        return nouls.get(qid)
    return None


def build_questions(window: str, candidates: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """One System One request: label each candidate; pick a rel for each compatible pair."""
    from typesafe_sdk import Choice, Noul

    questions: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    label_criteria = {name: LABEL_CRITERIA.get(name) for name in BUSINESS_LABELS}
    label_criteria[NONE] = LABEL_CRITERIA[NONE]

    for cand in candidates:
        qid = _qid("label", cand["key"])
        questions[qid] = Choice(
            instructions={
                "question": (
                    "What scm-v1 node type is `candidate.key` in `window`? "
                    "Use the key and surrounding sentences only. Choose none if it is not "
                    "a real business entity mention."
                ),
                "candidate": cand,
                "hint_from_key_shape": cand["default_label"],
            },
            criteria=label_criteria,
        )
        meta[qid] = {"kind": "label", "key": cand["key"]}

    pair_n = 0
    for src in candidates:
        for dst in candidates:
            if src["key"] == dst["key"]:
                continue
            options = rel_options(src["default_label"], dst["default_label"])
            if not options:
                continue
            if pair_n >= MAX_PAIR_QUESTIONS:
                break
            pair_n += 1
            rel_qid = _qid("rel", src["key"], dst["key"])
            stated_qid = _qid("stated", src["key"], dst["key"])
            criteria = {rel: REL_CRITERIA.get(rel, rel) for rel in options if rel in REL_TYPES}
            criteria[NONE] = (
                "The window does not state a directed ontology relationship "
                f"from {src['key']} to {dst['key']}."
            )
            questions[rel_qid] = Choice(
                instructions={
                    "question": (
                        "Which scm-v1 relationship does `window` state from `src.key` "
                        "to `dst.key`? Choose none if they are only co-mentioned or the "
                        "direction is the other way."
                    ),
                    "src": src,
                    "dst": dst,
                    "allowed_relationships": options,
                },
                criteria=criteria,
            )
            questions[stated_qid] = Noul(
                instructions={
                    "question": (
                        "Does `window` explicitly state a supply-chain relationship "
                        "from `src.key` to `dst.key`, not merely mention both names?"
                    ),
                    "src": src,
                    "dst": dst,
                },
                criteria={
                    "true": "The window asserts a factual link between these two keys.",
                    "false": "The keys appear together without a stated relationship.",
                },
            )
            meta[rel_qid] = {"kind": "rel", "src": src["key"], "dst": dst["key"]}
            meta[stated_qid] = {"kind": "stated", "src": src["key"], "dst": dst["key"]}
        if pair_n >= MAX_PAIR_QUESTIONS:
            break
    return questions, meta


def interpret_response(
    response: Any,
    *,
    candidates: list[dict[str, str]],
    meta: dict[str, Any],
    window: str,
) -> dict[str, Any]:
    by_key = {c["key"]: c for c in candidates}
    entities: list[dict[str, Any]] = []
    accepted: dict[str, str] = {}
    for qid, info in meta.items():
        if info.get("kind") != "label":
            continue
        ans = _choice_answer(response, qid)
        if ans is None:
            continue
        choice = str(getattr(ans, "choice", "") or "")
        confidence = float(getattr(ans, "confidence", 0.0) or 0.0)
        key = info["key"]
        cand = by_key.get(key)
        if not cand:
            continue
        if choice == NONE or choice not in BUSINESS_LABELS or confidence < LABEL_CONFIDENCE_MIN:
            continue
        accepted[key] = choice
        entities.append({
            "key": key,
            "label": choice,
            "name": cand["name"],
            "confidence": confidence,
        })

    relations: list[dict[str, Any]] = []
    for qid, info in meta.items():
        if info.get("kind") != "rel":
            continue
        src, dst = info["src"], info["dst"]
        if src not in accepted or dst not in accepted:
            continue
        ans = _choice_answer(response, qid)
        if ans is None:
            continue
        rel = str(getattr(ans, "choice", "") or "")
        confidence = float(getattr(ans, "confidence", 0.0) or 0.0)
        if rel == NONE or rel not in REL_TYPES or rel in {"MENTIONS", "DERIVED_FROM"}:
            continue
        if confidence < REL_CONFIDENCE_MIN:
            continue
        allowed = rel_options(accepted[src], accepted[dst])
        if rel not in allowed:
            continue
        stated_qid = _qid("stated", src, dst)
        stated = _noul_answer(response, stated_qid)
        p_yes = float(getattr(stated, "noul", 1.0) or 0.0) if stated is not None else 1.0
        if p_yes < STATED_NOUL_MIN:
            continue
        relations.append({
            "src": src,
            "rel": rel,
            "dst": dst,
            "confidence": round(confidence, 4),
            "stated": round(p_yes, 4),
        })

    cleaned = sanitize({"entities": entities, "relations": relations}, window)
    cleaned["skipped"] = False
    cleaned["reason"] = ""
    cleaned["model"] = getattr(response, "model", None)
    usage = getattr(response, "usage", None)
    if usage is not None:
        cleaned["usage"] = {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }
    return cleaned


def _default_system_one(state: Any, questions: dict[str, Any]) -> Any:
    from typesafe_sdk import TypeSafeClient

    load_typesafe_env()
    with TypeSafeClient(model="jev-latest", timeout=60.0) as client:
        return client.system_one(state=state, questions=questions)


def extract_from_text(text: str, *, system_one_fn: SystemOneFn | None = None) -> dict[str, Any]:
    windows = iter_windows(text)
    if not windows:
        return {"entities": [], "relations": [], "skipped": True, "reason": "empty", "windows": 0}
    if not jev_ingest_enabled():
        return {"entities": [], "relations": [], "skipped": True, "reason": "jev_ingest_disabled", "windows": 0}

    fn = system_one_fn or _default_system_one
    all_entities: list[dict[str, Any]] = []
    all_relations: list[dict[str, Any]] = []
    seen_ent: set[str] = set()
    seen_rel: set[tuple[str, str, str]] = set()
    last_error = ""
    any_ok = False
    model = None
    usage_in = 0
    usage_out = 0

    for window in windows:
        candidates = find_candidates(window)
        if not candidates:
            continue
        questions, meta = build_questions(window, candidates)
        if not questions:
            continue
        state = {
            "window": window,
            "ontology_version": "scm-v1",
            "candidates": candidates,
        }
        try:
            response = fn(state, questions)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or "typesafe_unavailable"
            continue
        extracted = interpret_response(response, candidates=candidates, meta=meta, window=window)
        any_ok = True
        model = extracted.get("model") or model
        usage = extracted.get("usage") or {}
        usage_in += int(usage.get("input_tokens") or 0)
        usage_out += int(usage.get("output_tokens") or 0)
        for ent in extracted.get("entities") or []:
            if ent["key"] in seen_ent:
                continue
            seen_ent.add(ent["key"])
            all_entities.append(ent)
        for rel in extracted.get("relations") or []:
            sig = (rel["src"], rel["rel"], rel["dst"])
            if sig in seen_rel:
                continue
            seen_rel.add(sig)
            all_relations.append(rel)

    if not any_ok:
        return {
            "entities": [],
            "relations": [],
            "skipped": True,
            "reason": last_error or "no_candidates",
            "windows": len(windows),
        }
    return {
        "entities": all_entities,
        "relations": all_relations,
        "skipped": False,
        "reason": "",
        "windows": len(windows),
        "model": model,
        "usage": {"input_tokens": usage_in, "output_tokens": usage_out},
    }


def extract_and_apply(
    text: str,
    *,
    document_id: str,
    doc_node_id: str,
    system_one_fn: SystemOneFn | None = None,
) -> dict[str, Any]:
    extracted = extract_from_text(text, system_one_fn=system_one_fn)
    if extracted.get("skipped"):
        return {
            "entities": 0,
            "relations": 0,
            "skipped": True,
            "reason": extracted.get("reason") or "",
            "windows": extracted.get("windows") or 0,
        }
    counts = apply_extraction(
        extracted,
        document_id=document_id,
        doc_node_id=doc_node_id,
        source_text=text,
        extractor="jev",
    )
    return {
        "entities": int(counts.get("entities") or 0),
        "relations": int(counts.get("relations") or 0),
        "skipped": False,
        "reason": "",
        "windows": extracted.get("windows") or 0,
        "model": extracted.get("model"),
        "payload": {"entities": extracted["entities"], "relations": extracted["relations"]},
    }


def apply_neo4j(
    payload: dict[str, Any],
    *,
    document_id: str,
    title: str,
    source_uri: str,
    source_text: str,
    tenant_id: str | None = None,
) -> dict[str, int]:
    """MERGE accepted facts into Neo4j only (JevTest / no Postgres)."""
    from graphrag.config import settings
    from graphrag.neo4j_client import client as neo4j

    tenant = tenant_id or settings.tenant_id
    cleaned = sanitize(payload, source_text)
    neo4j.merge_node(
        "Document",
        document_id,
        title,
        {"source_uri": source_uri, "extractor": "jev", "current": True},
    )
    doc_nid = f"{tenant}:Document:{document_id}"
    ids: dict[str, str] = {}
    for ent in cleaned["entities"]:
        neo4j.merge_node(ent["label"], ent["key"], ent["name"], {"extractor": "jev"})
        nid = f"{tenant}:{ent['label']}:{ent['key']}"
        ids[ent["key"]] = nid
        neo4j.merge_edge("MENTIONS", doc_nid, nid, float(ent.get("confidence") or 1.0))
        neo4j.merge_edge("DERIVED_FROM", nid, doc_nid, 1.0)
    wrote = 0
    for rel in cleaned["relations"]:
        src = ids.get(rel["src"])
        dst = ids.get(rel["dst"])
        if not src or not dst:
            continue
        neo4j.merge_edge(rel["rel"], src, dst, float(rel.get("confidence") or 1.0))
        wrote += 1
    return {"entities": len(cleaned["entities"]), "relations": wrote, "document": 1}
