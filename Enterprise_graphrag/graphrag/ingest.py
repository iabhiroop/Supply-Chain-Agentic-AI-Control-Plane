"""Idempotent document ingest: hash, chunk, embed, link MENTIONS."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from typing import Any

from psycopg.types.json import Json

from graphrag.config import settings
from graphrag.db import fetch_one, get_conn
from graphrag.embed import embed_texts
from graphrag.graph_store import ensure_document_in_neo4j, merge_edge, merge_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus")
TWIN_CORPUS = os.path.join(os.path.dirname(ROOT), "Supply_Chain_Digital_Twin", "corpus")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_text(text: str, *, heading: str | None = None, anchor_prefix: str | None = None) -> list[dict[str, Any]]:
    parts = re.split(r"\n(?=#{1,3} )", text.strip()) if text and "#" in text[:2000] else (text or "").split("\n\n")
    chunks = []
    ordinal = 0
    buf = ""
    prefix = f"{anchor_prefix}#" if anchor_prefix else ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        head = None
        m = re.match(r"^(#{1,3})\s+(.*)", piece)
        if m:
            head = m.group(2).strip()
        buf = (buf + "\n\n" + piece).strip() if buf else piece
        if len(buf) >= 280 or m:
            chunks.append({
                "ordinal": ordinal,
                "heading": head or heading,
                "text": buf,
                "anchor": f"{prefix}p{ordinal}",
            })
            ordinal += 1
            buf = ""
    if buf:
        chunks.append({
            "ordinal": ordinal,
            "heading": heading,
            "text": buf,
            "anchor": f"{prefix}p{ordinal}",
        })
    if not chunks and (text or "").strip():
        chunks = [{
            "ordinal": 0,
            "heading": heading,
            "text": text[:2000],
            "anchor": f"{prefix}p0",
        }]
    return chunks


def chunk_pages(pages: list[dict[str, Any]], *, heading: str | None = None) -> list[dict[str, Any]]:
    """Split by page first, then size. Anchors look like page:2#p0."""
    chunks: list[dict[str, Any]] = []
    ordinal = 0
    for page in pages:
        n = int(page.get("page") or 1)
        body = (page.get("text") or "").strip()
        if not body:
            continue
        inner = chunk_text(body, heading=heading or f"Page {n}", anchor_prefix=f"page:{n}")
        for ch in inner:
            ch["ordinal"] = ordinal
            chunks.append(ch)
            ordinal += 1
    return chunks


def ingest_document(
    *,
    source_uri: str,
    text: str,
    title: str | None = None,
    doc_type: str = "md",
    metadata: dict | None = None,
    supersede_id: str | None = None,
    pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    digest = _hash(text)
    existing = fetch_one(
        "SELECT document_id FROM documents WHERE tenant_id = %s AND content_hash = %s",
        (settings.tenant_id, digest),
    )
    if existing and not supersede_id:
        ensured = ensure_document_in_neo4j(existing["document_id"])
        return {
            "ok": True,
            "document_id": existing["document_id"],
            "deduped": True,
            "chunks": int(ensured.get("chunks") or 0),
            "interpreted": False,
            "windows": 0,
            "neo4j": ensured,
            "llm": {"entities": 0, "relations": 0, "skipped": True, "reason": "deduped", "windows": 0},
        }

    prior = fetch_one(
        """
        SELECT document_id FROM documents
         WHERE tenant_id = %s AND source_uri = %s AND current IS TRUE
         ORDER BY created_at DESC LIMIT 1
        """,
        (settings.tenant_id, source_uri),
    )
    if prior and not supersede_id:
        supersede_id = prior["document_id"]

    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, settings.tenant_id + digest))
    chunks = chunk_pages(pages, heading=title) if pages else chunk_text(text, heading=title)
    if not chunks:
        chunks = chunk_text(text or " ", heading=title)
    embeddings = embed_texts([c["text"] for c in chunks])
    written_chunks: list[tuple] = []
    with get_conn() as conn:
        if supersede_id:
            conn.execute(
                "UPDATE documents SET current = FALSE, superseded_by = %s WHERE document_id = %s",
                (document_id, supersede_id),
            )
        conn.execute(
            """
            INSERT INTO documents (document_id, tenant_id, source_uri, doc_type, title, content_hash, metadata, current)
            VALUES (%s,%s,%s,%s,%s,%s,%s, TRUE)
            ON CONFLICT (document_id) DO UPDATE SET title = EXCLUDED.title
            """,
            (document_id, settings.tenant_id, source_uri, doc_type, title or source_uri, digest, Json(metadata or {})),
        )
        conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))
        for ch, emb in zip(chunks, embeddings, strict=False):
            cid = str(uuid.uuid5(uuid.NAMESPACE_URL, document_id + str(ch["ordinal"])))
            conn.execute(
                """
                INSERT INTO chunks (chunk_id, document_id, tenant_id, ordinal, heading, text, anchor, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (cid, document_id, settings.tenant_id, ch["ordinal"], ch.get("heading"), ch["text"], ch["anchor"], emb),
            )
            written_chunks.append((cid, ch, emb))
    doc_node = merge_node("Document", document_id, title or source_uri, {
        "source_uri": source_uri,
        "hash": digest,
        "doc_type": doc_type,
        "current": True,
    })
    for cid, ch, emb in written_chunks:
        props = {
            "document_id": document_id,
            "text": (ch.get("text") or "")[:8000],
            "heading": ch.get("heading"),
            "anchor": ch.get("anchor"),
            "ordinal": int(ch.get("ordinal") or 0),
            "title": title or source_uri,
            "source_uri": source_uri,
        }
        if isinstance(emb, list) and emb:
            props["embedding"] = [float(x) for x in emb]
        chunk_nid = merge_node("Chunk", cid, ch.get("heading") or cid, props)
        merge_edge("CONTAINS", doc_node, chunk_nid)
        merge_edge("DERIVED_FROM", chunk_nid, doc_node)
    from graphrag.ontology import extract_entity_keys, label_for_key

    keys = extract_entity_keys(text)
    if re.search(r"speedy express", text, re.I):
        keys.append("Speedy Express")
    for key_u in keys:
        label = "Carrier" if key_u == "Speedy Express" else label_for_key(key_u)
        nid = merge_node(label, key_u, key_u)
        merge_edge("MENTIONS", doc_node, nid, provenance={"document_id": document_id})
        merge_edge("DERIVED_FROM", nid, doc_node, provenance={"document_id": document_id})
    llm_counts = {"entities": 0, "relations": 0, "skipped": True, "reason": "not_run", "windows": 0}
    extractor = (os.getenv("GRAPHRAG_EXTRACTOR") or "llm").strip().lower()
    try:
        if extractor == "jev":
            from graphrag.jev_extract import extract_and_apply as extract_and_apply

            llm_counts = extract_and_apply(text, document_id=document_id, doc_node_id=doc_node)
        else:
            from graphrag.extract import extract_and_apply

            llm_counts = extract_and_apply(text, document_id=document_id, doc_node_id=doc_node)
            extractor = "llm"
    except Exception:  # noqa: BLE001
        llm_counts = {"entities": 0, "relations": 0, "skipped": True, "reason": "extract_error", "windows": 0}
    windows = int(llm_counts.get("windows") or 0)
    skipped = bool(llm_counts.get("skipped"))
    return {
        "ok": True,
        "document_id": document_id,
        "deduped": False,
        "chunks": len(chunks),
        "ocr_pages": int((metadata or {}).get("ocr_pages") or 0),
        "engine": (metadata or {}).get("engine"),
        "interpreted": not skipped,
        "windows": windows,
        "extractor": extractor,
        "llm": {
            "backend": extractor,
            "entities": int(llm_counts.get("entities") or 0),
            "relations": int(llm_counts.get("relations") or 0),
            "skipped": skipped,
            "reason": llm_counts.get("reason") or "",
            "windows": windows,
        },
    }


BINARY_EXTS = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
TEXT_EXTS = {".md", ".txt", ".csv", ".json"}


def ingest_path(path: str) -> dict[str, Any]:
    from graphrag.parse import parse_document

    parsed = parse_document(path)
    return ingest_parsed(parsed, source_uri=os.path.abspath(path), title=os.path.basename(path))


def ingest_bytes(data: bytes, *, filename: str, source_uri: str | None = None, title: str | None = None) -> dict[str, Any]:
    from graphrag.parse import parse_document

    parsed = parse_document(data=data, filename=filename)
    uri = source_uri or f"upload://{filename}"
    return ingest_parsed(parsed, source_uri=uri, title=title or filename)


def ingest_parsed(parsed: dict[str, Any], *, source_uri: str, title: str | None = None) -> dict[str, Any]:
    pages = parsed.get("pages") or []
    text = parsed.get("text") or ""
    name = parsed.get("filename") or title or source_uri
    ext = os.path.splitext(name)[1].lstrip(".").lower() or "txt"
    dotted = os.path.splitext(name)[1].lower()
    ocr_pages = sum(1 for p in pages if p.get("ocr"))
    metadata = {
        "pages": len(pages),
        "ocr_pages": ocr_pages,
        "engine": parsed.get("engine"),
        "warnings": parsed.get("warnings") or [],
    }
    if dotted in BINARY_EXTS and not (text or "").strip():
        return {
            "ok": False,
            "reason": "empty_parse",
            "document_id": None,
            "deduped": False,
            "chunks": 0,
            "interpreted": False,
            "windows": 0,
            "warnings": metadata["warnings"],
            "engine": parsed.get("engine"),
            "ocr_pages": ocr_pages,
            "llm": {"entities": 0, "relations": 0, "skipped": True, "reason": "empty_parse", "windows": 0},
        }
    result = ingest_document(
        source_uri=source_uri,
        text=text,
        title=title or name,
        doc_type=ext,
        metadata=metadata,
        pages=pages or None,
    )
    result["warnings"] = metadata["warnings"]
    result["engine"] = parsed.get("engine")
    return result


def ingest_corpus() -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ingest_jobs (job_id, tenant_id, status, source) VALUES (%s,%s,'RUNNING',%s)",
            (job_id, settings.tenant_id, CORPUS),
        )
    results = []
    for folder in (CORPUS, TWIN_CORPUS):
        if not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                ext = os.path.splitext(name)[1].lower()
                if ext in TEXT_EXTS | BINARY_EXTS:
                    results.append(ingest_path(os.path.join(root, name)))
    counts = {
        "documents": len(results),
        "chunks": sum(r.get("chunks") or 0 for r in results),
        "deduped": sum(1 for r in results if r.get("deduped")),
        "ocr_pages": sum(int(r.get("ocr_pages") or 0) for r in results),
    }
    with get_conn() as conn:
        conn.execute(
            "UPDATE ingest_jobs SET status='DONE', counts=%s, finished_at=now() WHERE job_id=%s",
            (Json(counts), job_id),
        )
    return {"job_id": job_id, "counts": counts, "results": results}
