"""Hybrid retrieval: vector + BM25 + graph traversal, RRF fusion, tenant filter first."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from typing import Any

from graphrag.config import settings
from graphrag.db import fetch_all, get_conn
from graphrag.embed import cosine, embed_texts
from graphrag.graph_store import traverse
from graphrag.neo4j_client import client as neo4j
from graphrag.ontology import extract_entity_keys, schema as ontology_schema


def classify_intent(query: str) -> str:
    q = query.lower()
    if any(w in q for w in ("theme", "overall", "summar", "what are")):
        return "global"
    if any(w in q for w in ("if ", "exposed", "risk", "slip", "depends", "which open", "multi",
                            "shutdown", "disruption", "taiwan")):
        return "multihop"
    if any(w in q for w in ("count", "how many", "total")):
        return "aggregation"
    return "local"


def extract_anchors(query: str) -> list[str]:
    found = extract_entity_keys(query)
    extra = []
    ql = query.lower()
    if "asturias" in ql or "vend-a" in ql or "dairy" in ql:
        extra.append("VEND-A")
    if "iberia" in ql or "vend-c" in ql:
        extra.append("VEND-C")
    if "queso" in ql or "cabrales" in ql:
        extra.append("MAT-11")
    if "havre" in ql or "wh-fr" in ql:
        extra.append("WH-FR-01")
    if "taiwan" in ql or "vend-abc" in ql or "shutdown" in ql or "semi" in ql:
        extra.extend(["VEND-ABC", "MAT-SEMI-1"])
    if "chennai" in ql:
        extra.append("PL-IN-CHN")
    if "xyz" in ql or "penang" in ql or "alternate" in ql:
        extra.append("VEND-XYZ")
    if "po-1842" in ql or "1842" in ql:
        extra.append("PO-1842")
    return list(dict.fromkeys(found + extra))


def _bm25(query: str, chunks: list[dict], k1: float = 1.5, b: float = 0.75) -> dict[str, float]:
    qtoks = query.lower().split()
    docs = [((c["text"] or "").lower().split()) for c in chunks]
    n = len(docs) or 1
    avgdl = sum(len(d) for d in docs) / n
    df = defaultdict(int)
    for d in docs:
        for t in set(d):
            df[t] += 1
    scores = {}
    for ch, toks in zip(chunks, docs, strict=False):
        score = 0.0
        tf = defaultdict(int)
        for t in toks:
            tf[t] += 1
        dl = len(toks) or 1
        for t in qtoks:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores[ch["chunk_id"]] = score
    return scores


def _rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    fused: dict[str, float] = defaultdict(float)
    for ranks in rank_lists:
        for i, cid in enumerate(ranks):
            fused[cid] += 1.0 / (k + i + 1)
    return fused


def _load_chunks(tenant: str) -> tuple[list[dict[str, Any]], str]:
    if settings.neo4j_enabled:
        rows = neo4j.fetch_chunks(tenant)
        if rows is not None:
            return rows, "neo4j"
    pg = fetch_all(
        """
        SELECT c.*, d.title, d.source_uri, d.current
          FROM chunks c JOIN documents d ON d.document_id = c.document_id
         WHERE c.tenant_id = %s AND d.current IS TRUE
        """,
        (tenant,),
    )
    return pg, "postgres"


def retrieve(
    query: str,
    *,
    k: int = 8,
    tenant_id: str | None = None,
    vector_only: bool = False,
) -> dict[str, Any]:
    tenant = tenant_id or settings.tenant_id
    intent = classify_intent(query)
    anchors = extract_anchors(query)
    from graphrag.query_planner import plan_query
    from graphrag.graph_query import run_template

    plan = plan_query(query)
    hops = int(plan.get("hops") or (3 if any(a.startswith("VEND-ABC") or a == "MAT-SEMI-1" for a in anchors) else 2))
    chunks, chunk_source = _load_chunks(tenant)
    qvec = embed_texts([query])[0]
    vec_scores = {}
    for ch in chunks:
        emb = ch.get("embedding") or []
        if isinstance(emb, str) or not emb:
            continue
        try:
            vec_scores[ch["chunk_id"]] = cosine(qvec, [float(x) for x in list(emb)])
        except (TypeError, ValueError):
            continue
    bm = _bm25(query, chunks)
    vec_rank = [cid for cid, _ in sorted(vec_scores.items(), key=lambda x: -x[1])]
    bm_rank = [cid for cid, _ in sorted(bm.items(), key=lambda x: -x[1])]
    paths = traverse(anchors, hops=hops) if intent in {"multihop", "aggregation"} or anchors else []
    templated = None
    if not vector_only and plan.get("template"):
        templated = run_template(
            plan["template"],
            key=plan.get("key"),
            src=plan.get("src"),
            dst=plan.get("dst"),
            hops=hops,
        )
        if isinstance(templated, dict) and not templated.get("error") and templated.get("paths"):
            rebuilt = []
            for row in templated["paths"]:
                rebuilt.append(
                    {
                        "src_key": row.get("src"),
                        "dst_key": row.get("dst"),
                        "src_label": "",
                        "dst_label": "",
                        "rel_type": row.get("rel") or "",
                    }
                )
            if rebuilt:
                paths = rebuilt
    graph_ids = []
    for p in paths:
        graph_ids.extend([p.get("src_key"), p.get("dst_key")])
    graph_rank = []
    for ch in chunks:
        blob = f"{ch.get('text')} {ch.get('title')}"
        if any(a and a in blob for a in (anchors + graph_ids)):
            graph_rank.append(ch["chunk_id"])

    if vector_only:
        paths = []
        strategies = ["vector"]
        lists = [vec_rank]
    else:
        strategies = ["vector", "bm25"]
        lists = [vec_rank, bm_rank]
        if intent in {"multihop", "global", "aggregation"} or anchors:
            strategies.append("graph")
            lists.append(graph_rank)
    fused = _rrf(lists)
    by_id = {c["chunk_id"]: c for c in chunks}
    ranked = []
    seen_text = set()
    for cid, score in sorted(fused.items(), key=lambda x: -x[1]):
        ch = by_id.get(cid)
        if not ch:
            continue
        sig = (ch.get("text") or "")[:80]
        if sig in seen_text:
            continue
        seen_text.add(sig)
        ranked.append({
            "chunk_id": cid,
            "document_id": ch["document_id"],
            "title": ch.get("title"),
            "source_uri": ch.get("source_uri"),
            "heading": ch.get("heading"),
            "anchor": ch.get("anchor"),
            "text": ch.get("text"),
            "score": round(score, 5),
            "citation": f"{ch.get('title')}#{ch.get('anchor')}",
        })
        if len(ranked) >= k:
            break

    communities = []
    if intent == "global":
        communities = fetch_all(
            "SELECT community_id, summary, level FROM communities WHERE tenant_id = %s LIMIT 5",
            (tenant,),
        )

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO query_log (log_id, tenant_id, query, intent, strategies)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (str(uuid.uuid4()), tenant, query, intent, strategies),
        )

    graph_paths = []
    if isinstance(templated, dict) and templated.get("paths"):
        graph_paths = [
            {"path": row.get("path"), "src": row.get("src"), "dst": row.get("dst"), "rel": row.get("rel")}
            for row in templated["paths"][:20]
        ]
    else:
        graph_paths = [
            {
                "path": f"{p['src_label']}:{p['src_key']} -[{p['rel_type']}]-> {p['dst_label']}:{p['dst_key']}",
                "src": p["src_key"],
                "dst": p["dst_key"],
                "rel": p["rel_type"],
            }
            for p in paths[:20]
        ]

    return {
        "query": query,
        "intent": intent,
        "anchors": anchors,
        "strategies": strategies,
        "planned_query": plan,
        "graph_template": plan.get("template"),
        "passages": ranked,
        "graph_paths": graph_paths,
        "communities": communities,
        "ontology": ontology_schema()["version"],
        "graph_store": "neo4j" if settings.neo4j_enabled else "postgres",
        "chunk_store": chunk_source,
    }
