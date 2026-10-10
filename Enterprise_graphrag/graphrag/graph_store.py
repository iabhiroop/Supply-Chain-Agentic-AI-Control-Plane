"""Graph nodes/edges dual-written to Postgres; Neo4j is the primary read store."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from psycopg.types.json import Json

from graphrag.config import settings
from graphrag.db import fetch_all, fetch_one, get_conn
from graphrag.neo4j_client import client as neo4j
from graphrag.ontology import NODE_TYPES, REL_TYPES

logger = logging.getLogger(__name__)


def node_id(label: str, key: str, tenant: str | None = None) -> str:
    tenant = tenant or settings.tenant_id
    return f"{tenant}:{label}:{key}"


def merge_node(
    label: str,
    key: str,
    name: str | None = None,
    properties: dict | None = None,
    *,
    to_neo4j: bool = True,
) -> str:
    if label not in NODE_TYPES:
        raise ValueError(f"Off-ontology label {label}")
    nid = node_id(label, key)
    props = properties or {}
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO nodes (node_id, tenant_id, label, key, name, properties)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, label, key) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, nodes.name),
                properties = nodes.properties || EXCLUDED.properties
            """,
            (nid, settings.tenant_id, label, key, name or key, Json(props)),
        )
    if to_neo4j:
        try:
            neo4j.merge_node(label, key, name or key, props)
        except Exception:  # noqa: BLE001
            logger.warning("Neo4j merge_node skipped for %s:%s", label, key)
    return nid


def merge_edge(
    rel_type: str,
    src_id: str,
    dst_id: str,
    confidence: float = 1.0,
    provenance: dict | None = None,
    *,
    to_neo4j: bool = True,
) -> str:
    if rel_type not in REL_TYPES:
        raise ValueError(f"Off-ontology rel {rel_type}")
    eid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{src_id}|{rel_type}|{dst_id}"))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO edges (edge_id, tenant_id, rel_type, src_id, dst_id, confidence, provenance)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, rel_type, src_id, dst_id) DO UPDATE SET
                confidence = GREATEST(edges.confidence, EXCLUDED.confidence)
            """,
            (eid, settings.tenant_id, rel_type, src_id, dst_id, confidence, Json(provenance or {})),
        )
    if to_neo4j:
        try:
            neo4j.merge_edge(rel_type, src_id, dst_id, confidence)
        except Exception:  # noqa: BLE001
            logger.warning("Neo4j merge_edge skipped for %s", rel_type)
    return eid


def get_entity(entity_id: str) -> dict[str, Any] | None:
    if settings.neo4j_enabled:
        row = neo4j.get_entity(entity_id)
        if row is not None:
            return row
    node = fetch_one(
        "SELECT * FROM nodes WHERE node_id = %s AND tenant_id = %s",
        (entity_id, settings.tenant_id),
    )
    if not node:
        node = fetch_one(
            "SELECT * FROM nodes WHERE key = %s AND tenant_id = %s LIMIT 1",
            (entity_id, settings.tenant_id),
        )
    if not node:
        return None
    nid = node["node_id"]
    out_e = fetch_all(
        """
        SELECT e.rel_type, n.node_id, n.label, n.key, n.name
          FROM edges e JOIN nodes n ON n.node_id = e.dst_id
         WHERE e.src_id = %s AND e.tenant_id = %s
        """,
        (nid, settings.tenant_id),
    )
    in_e = fetch_all(
        """
        SELECT e.rel_type, n.node_id, n.label, n.key, n.name
          FROM edges e JOIN nodes n ON n.node_id = e.src_id
         WHERE e.dst_id = %s AND e.tenant_id = %s
        """,
        (nid, settings.tenant_id),
    )
    return {**node, "outgoing": out_e, "incoming": in_e, "source": "postgres"}


def traverse(anchor_keys: list[str], hops: int = 2) -> list[dict[str, Any]]:
    """Neo4j first. Postgres only if Neo4j is down (None)."""
    if not anchor_keys:
        return []
    if settings.neo4j_enabled:
        rows = neo4j.traverse(anchor_keys, hops=hops)
        if rows is not None:
            return rows
    return _traverse_pg(anchor_keys, hops)


def _traverse_pg(anchor_keys: list[str], hops: int) -> list[dict[str, Any]]:
    nodes = fetch_all(
        """
        SELECT * FROM nodes
         WHERE tenant_id = %s AND key = ANY(%s)
        """,
        (settings.tenant_id, anchor_keys),
    )
    seen = {n["node_id"] for n in nodes}
    frontier = list(seen)
    paths: list[dict[str, Any]] = []
    for _ in range(max(1, hops)):
        nxt = []
        for fid in frontier:
            rows = fetch_all(
                """
                SELECT e.rel_type, e.src_id, e.dst_id,
                       s.label AS src_label, s.key AS src_key, s.name AS src_name,
                       d.label AS dst_label, d.key AS dst_key, d.name AS dst_name
                  FROM edges e
                  JOIN nodes s ON s.node_id = e.src_id
                  JOIN nodes d ON d.node_id = e.dst_id
                 WHERE e.tenant_id = %s AND (e.src_id = %s OR e.dst_id = %s)
                """,
                (settings.tenant_id, fid, fid),
            )
            for row in rows:
                paths.append(row)
                for nid in (row["src_id"], row["dst_id"]):
                    if nid not in seen:
                        seen.add(nid)
                        nxt.append(nid)
        frontier = nxt
        if not frontier:
            break
    return paths


def sync_to_neo4j() -> dict[str, int]:
    if not settings.neo4j_enabled:
        return {"nodes": 0, "edges": 0, "skipped": 1}
    chunk_n = _project_chunks_into_graph()
    nodes = fetch_all("SELECT * FROM nodes WHERE tenant_id = %s", (settings.tenant_id,))
    edges = fetch_all("SELECT * FROM edges WHERE tenant_id = %s", (settings.tenant_id,))
    result = neo4j.bulk_sync(nodes, edges)
    result["chunks_projected"] = chunk_n
    result["source"] = "neo4j"
    return result


def ensure_document_in_neo4j(document_id: str) -> dict[str, Any]:
    """MERGE one document's Document/Chunk nodes into Neo4j (used on hash dedup)."""
    if not settings.neo4j_enabled:
        return {"chunks": 0, "skipped": 1}
    doc = fetch_one(
        """
        SELECT document_id, title, source_uri, current, content_hash, doc_type
          FROM documents WHERE document_id = %s AND tenant_id = %s
        """,
        (document_id, settings.tenant_id),
    )
    if not doc:
        return {"chunks": 0, "missing": 1}
    n = _project_one_document(doc, to_neo4j=True)
    return {"chunks": n, "document_id": document_id}


def _project_chunks_into_graph() -> int:
    """Copy document text chunks onto Chunk nodes so Neo4j holds the corpus too."""
    docs = fetch_all(
        """
        SELECT document_id, title, source_uri, current, content_hash, doc_type
          FROM documents WHERE tenant_id = %s
        """,
        (settings.tenant_id,),
    )
    n = 0
    for doc in docs:
        n += _project_one_document(doc, to_neo4j=False)
    return n


def _project_one_document(doc: dict[str, Any], *, to_neo4j: bool) -> int:
    doc_nid = merge_node(
        "Document",
        doc["document_id"],
        doc.get("title") or doc["document_id"],
        {
            "source_uri": doc.get("source_uri"),
            "hash": doc.get("content_hash"),
            "doc_type": doc.get("doc_type"),
            "current": bool(doc.get("current")),
        },
        to_neo4j=to_neo4j,
    )
    chunks = fetch_all(
        """
        SELECT chunk_id, ordinal, heading, text, anchor, embedding
          FROM chunks WHERE document_id = %s AND tenant_id = %s
         ORDER BY ordinal
        """,
        (doc["document_id"], settings.tenant_id),
    )
    n = 0
    for ch in chunks:
        emb = ch.get("embedding")
        if hasattr(emb, "tolist"):
            emb = list(emb)
        elif isinstance(emb, str):
            emb = None
        props = {
            "document_id": doc["document_id"],
            "text": (ch.get("text") or "")[:8000],
            "heading": ch.get("heading"),
            "anchor": ch.get("anchor"),
            "ordinal": int(ch.get("ordinal") or 0),
            "title": doc.get("title"),
            "source_uri": doc.get("source_uri"),
        }
        if isinstance(emb, list) and emb:
            props["embedding"] = [float(x) for x in emb]
        cid = merge_node(
            "Chunk",
            ch["chunk_id"],
            ch.get("heading") or ch["chunk_id"],
            props,
            to_neo4j=to_neo4j,
        )
        merge_edge("CONTAINS", doc_nid, cid, to_neo4j=to_neo4j)
        merge_edge("DERIVED_FROM", cid, doc_nid, to_neo4j=to_neo4j)
        n += 1
    return n
