"""GraphRAG HTTP API — port 8100."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from graphrag.answer import answer as synthesize
from graphrag.apply_schema import apply as apply_schema
from graphrag.communities import rebuild_communities
from graphrag.config import settings
from graphrag.db import fetch_one, get_conn
from graphrag.eval_emit import emit_retrieval
from graphrag.graph_query import run_template
from graphrag.graph_store import get_entity, merge_edge, merge_node, sync_to_neo4j
from graphrag.ingest import ingest_bytes, ingest_corpus, ingest_document, ingest_path
from graphrag.neo4j_client import client as neo4j
from graphrag.ontology import NODE_TYPES, schema as ontology_schema
from graphrag.project_experience import project_experience
from graphrag.project_scm import project_operational
from graphrag.retrieve import retrieve

app = FastAPI(title="Enterprise GraphRAG", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_STATIC = Path(__file__).resolve().parent.parent / "static"


class IngestIn(BaseModel):
    source_uri: str
    text: str
    title: str | None = None
    doc_type: str = "md"
    metadata: dict = Field(default_factory=dict)
    supersede_id: str | None = None


class BatchIn(BaseModel):
    corpus: bool = False
    paths: list[str] = Field(default_factory=list)
    documents: list[IngestIn] = Field(default_factory=list)


class RetrieveIn(BaseModel):
    query: str
    k: int = 8
    tenant_id: str | None = None
    vector_only: bool = False


class AnswerIn(BaseModel):
    query: str
    k: int = 8
    tenant_id: str | None = None


class GraphQueryIn(BaseModel):
    template: str
    key: str | None = None
    src: str | None = None
    dst: str | None = None
    hops: int = 2


class GraphPlanIn(BaseModel):
    query: str


class NodeIn(BaseModel):
    label: str
    key: str
    name: str | None = None
    properties: dict = Field(default_factory=dict)
    rel_type: str | None = None
    dst_key: str | None = None
    dst_label: str | None = None


class FeedbackIn(BaseModel):
    query: str | None = None
    relevant: bool | None = None
    comment: str | None = None


@app.on_event("startup")
def _startup() -> None:
    apply_schema()


@app.get("/health")
def health():
    reachable = neo4j.ping() if settings.neo4j_enabled else False
    return {
        "status": "ok",
        "service": "graphrag",
        "port": settings.port,
        "neo4j": {
            "enabled": settings.neo4j_enabled,
            "reachable": reachable,
            "error": neo4j.last_error if settings.neo4j_enabled and not reachable else None,
        },
        "ontology": ontology_schema()["version"],
    }


@app.post("/v1/ingest")
def post_ingest(body: IngestIn):
    return ingest_document(
        source_uri=body.source_uri,
        text=body.text,
        title=body.title,
        doc_type=body.doc_type,
        metadata=body.metadata,
        supersede_id=body.supersede_id,
    )


@app.post("/v1/ingest/batch")
def post_batch(body: BatchIn):
    results = []
    if body.corpus:
        return ingest_corpus()
    for path in body.paths:
        results.append(ingest_path(path))
    for doc in body.documents:
        results.append(ingest_document(
            source_uri=doc.source_uri, text=doc.text, title=doc.title,
            doc_type=doc.doc_type, metadata=doc.metadata, supersede_id=doc.supersede_id,
        ))
    return {"results": results, "counts": {"documents": len(results)}}


@app.post("/v1/ingest/file")
async def post_ingest_file(
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
):
    """Upload a PDF, Word file, image, or text document into the graph."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    name = file.filename or "upload.bin"
    return ingest_bytes(data, filename=name, title=title or name)


@app.get("/v1/ingest/jobs/{job_id}")
def get_job(job_id: str):
    row = fetch_one("SELECT * FROM ingest_jobs WHERE job_id = %s", (job_id,))
    if not row:
        raise HTTPException(404, "job not found")
    return row


@app.post("/v1/retrieve")
def post_retrieve(body: RetrieveIn, x_tenant_id: str | None = Header(default=None), x_trace_id: str | None = Header(default=None)):
    from graphrag.trace import bind_trace_id, reset_trace_id
    token = bind_trace_id(x_trace_id)
    try:
        tenant = body.tenant_id or x_tenant_id
        payload = retrieve(body.query, k=body.k, tenant_id=tenant, vector_only=body.vector_only)
        emit_retrieval("retrieve", body.query, payload)
        return payload
    finally:
        reset_trace_id(token)


@app.post("/v1/answer")
def post_answer(body: AnswerIn, x_tenant_id: str | None = Header(default=None), x_trace_id: str | None = Header(default=None)):
    from graphrag.trace import bind_trace_id, reset_trace_id
    token = bind_trace_id(x_trace_id)
    try:
        tenant = body.tenant_id or x_tenant_id
        payload = synthesize(body.query, tenant_id=tenant, k=body.k)
        emit_retrieval("answer", body.query, payload)
        return payload
    finally:
        reset_trace_id(token)


@app.post("/v1/graph/plan")
def post_graph_plan(body: GraphPlanIn):
    from graphrag.query_planner import plan_query

    return plan_query(body.query)


@app.post("/v1/graph/query")
def post_graph_query(body: GraphQueryIn, x_trace_id: str | None = Header(default=None)):
    from graphrag.trace import bind_trace_id, reset_trace_id
    token = bind_trace_id(x_trace_id)
    try:
        return run_template(body.template, key=body.key, src=body.src, dst=body.dst, hops=body.hops)
    finally:
        reset_trace_id(token)


@app.get("/v1/graph/entity/{entity_id}")
def get_graph_entity(entity_id: str):
    row = get_entity(entity_id)
    if not row:
        raise HTTPException(404, "entity not found")
    return row


@app.get("/v1/graph/schema")
def get_schema():
    return ontology_schema()


@app.post("/v1/graph/nodes")
def post_nodes(body: NodeIn):
    if body.label not in {"Incident", "Decision"}:
        raise HTTPException(400, "Agents may only write Incident or Decision nodes")
    nid = merge_node(body.label, body.key, body.name, body.properties)
    neo4j.merge_node(body.label, body.key, body.name or body.key, body.properties)
    edge = None
    if body.rel_type and body.dst_key:
        dst = merge_node(body.dst_label or "Order", body.dst_key)
        edge = merge_edge(body.rel_type, nid, dst)
        neo4j.merge_edge(body.rel_type, nid, dst)
    return {"node_id": nid, "edge_id": edge}


@app.post("/v1/feedback")
def post_feedback(body: FeedbackIn):
    fid = str(uuid4())
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback (feedback_id, tenant_id, query, relevant, comment) VALUES (%s,%s,%s,%s,%s)",
            (fid, settings.tenant_id, body.query, body.relevant, body.comment),
        )
    return {"feedback_id": fid}


@app.post("/v1/reindex")
def post_reindex():
    corpus = ingest_corpus()
    projected = project_operational()
    experience = project_experience()
    communities = rebuild_communities()
    graph = sync_to_neo4j()
    return {
        "corpus": corpus,
        "projected": projected,
        "experience": experience,
        "communities": communities,
        "neo4j": graph,
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/search", response_class=HTMLResponse)
def search_ui():
    html = _STATIC / "search.html"
    return HTMLResponse(html.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


def bootstrap() -> dict:
    apply_schema()
    corpus = ingest_corpus()
    projected = project_operational()
    experience = project_experience()
    communities = rebuild_communities()
    graph = sync_to_neo4j()
    return {
        "corpus": corpus,
        "projected": projected,
        "experience": experience,
        "communities": communities,
        "neo4j": graph,
    }


def main() -> None:
    import uvicorn

    bootstrap()
    print(f"GraphRAG on http://{settings.host}:{settings.port}/search")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
