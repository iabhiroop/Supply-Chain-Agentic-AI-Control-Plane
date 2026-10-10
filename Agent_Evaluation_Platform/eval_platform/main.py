"""HTTP API — Layer 4. Port 8002. Never calls Layer 2 tools; may read ERP snapshots."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from eval_platform.analytics import maybe_alert, quality
from eval_platform.annotations import add_annotation, add_feedback, queue
from eval_platform.apply_schema import apply as apply_schema
from eval_platform.collect_layer2 import ingest_transaction, persist_and_score
from eval_platform.config import settings
from eval_platform.experiments import compare, get_experiment, list_datasets, run_experiment, seed_golden
from eval_platform.gates import check_gate
from eval_platform.scorecard import last_scorecard
from eval_platform.store import get_trace, insert_run, list_runs, list_traces, upsert_trace

app = FastAPI(title="Agent Evaluation Platform", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC = Path(__file__).resolve().parent.parent / "static"


class SpanIn(BaseModel):
    span_id: str | None = None
    parent_span_id: str | None = None
    name: str = "span"
    kind: str = "internal"
    status: str = "OK"
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: float | None = None
    model: str | None = None
    prompt_id: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0
    cache_status: str | None = None
    input: dict | list | None = None
    output: dict | list | None = None
    error: str | None = None
    attributes: dict = Field(default_factory=dict)


class TraceIn(BaseModel):
    trace_id: str | None = None
    tenant_id: str = "default"
    workflow: str | None = None
    transaction_id: str | None = None
    status: str = "OK"
    started_at: str | None = None
    ended_at: str | None = None
    cost_usd: float = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    source: str = "http"
    metadata: dict = Field(default_factory=dict)
    spans: list[SpanIn] = Field(default_factory=list)
    run: dict | None = None


class BatchIn(BaseModel):
    traces: list[TraceIn]


class ScoreIn(BaseModel):
    transaction_id: str
    expected: dict | None = None
    judge_llm: bool = False


class ScoreDisruptionIn(BaseModel):
    recommendation: dict
    trace_id: str | None = None
    run_id: str | None = None
    expected: dict | None = None
    judge_llm: bool = False


class ExperimentIn(BaseModel):
    name: str = "adhoc"
    version_id: str | None = None
    judge_llm: bool = False


class AnnotationIn(BaseModel):
    run_id: str
    annotator: str = "operator"
    labels: dict
    rubric: dict | None = None


class FeedbackIn(BaseModel):
    run_id: str
    source: str = "operator"
    rating: int | None = None
    comment: str | None = None


def _store_trace(body: TraceIn) -> str:
    payload = body.model_dump()
    spans = payload.pop("spans", [])
    run = payload.pop("run", None)
    payload["spans"] = spans
    trace_id = upsert_trace(payload)
    if run:
        run["trace_id"] = trace_id
        insert_run(run)
    return trace_id


@app.on_event("startup")
def _startup():
    import logging

    try:
        apply_schema()
        seed_golden()
    except Exception:
        logging.getLogger("eval_platform").exception("eval schema/seed failed on startup")


@app.get("/health")
def health():
    return {"status": "ok", "service": "evaluation", "port": settings.port}


@app.post("/v1/traces")
def post_trace(body: TraceIn):
    return {"trace_id": _store_trace(body)}


@app.post("/v1/traces/batch")
def post_batch(body: BatchIn):
    ids = [_store_trace(t) for t in body.traces]
    return {"trace_ids": ids}


@app.post("/v1/traces/otlp")
def post_otlp(body: dict):
    """JSON OTLP-ish ingest. Gateway/agents may POST resourceSpans here."""
    ids = []
    for rs in body.get("resourceSpans") or [body]:
        resource = ((rs.get("resource") or {}).get("attributes")) or {}
        for ss in rs.get("scopeSpans") or [{}]:
            spans_in = []
            trace_id = None
            for sp in ss.get("spans") or []:
                trace_id = sp.get("traceId") or sp.get("trace_id") or trace_id
                spans_in.append({
                    "span_id": sp.get("spanId") or sp.get("span_id"),
                    "parent_span_id": sp.get("parentSpanId") or sp.get("parent_span_id"),
                    "name": sp.get("name") or "span",
                    "kind": str(sp.get("kind") or "internal"),
                    "status": (sp.get("status") or {}).get("code") or "OK",
                    "attributes": sp.get("attributes") or {},
                })
            if spans_in:
                ids.append(upsert_trace({
                    "trace_id": trace_id,
                    "source": "otlp",
                    "metadata": {"resource": resource},
                    "spans": spans_in,
                }))
    return {"trace_ids": ids}


@app.get("/v1/traces")
def traces(workflow: str | None = None, status: str | None = None, limit: int = 50):
    return {"traces": list_traces(limit, workflow, status)}


@app.get("/v1/traces/{trace_id}")
def trace_detail(trace_id: str):
    found = get_trace(trace_id)
    if not found:
        raise HTTPException(404, "Unknown trace")
    return found


@app.get("/v1/scorecard")
def scorecard(trace_id: str | None = None):
    """Last Taiwan run + last P2P golden, or one trace's rolled-up scores."""
    return last_scorecard(trace_id)


@app.get("/v1/runs")
def runs(limit: int = 40):
    return {"runs": list_runs(limit)}


@app.post("/v1/evaluations/score")
def score(body: ScoreIn):
    return ingest_transaction(body.transaction_id, expected=body.expected, judge_llm=body.judge_llm)


@app.post("/v1/evaluations/score-disruption")
def score_disruption(body: ScoreDisruptionIn):
    expected = dict(body.expected or {})
    expected.setdefault("workflow", "disruption")
    trace_id = body.trace_id or "adhoc-disruption"
    return persist_and_score(
        {"workflow": "disruption", "status": "RECOMMENDED", "recommendation": body.recommendation},
        expected=expected,
        judge_llm=body.judge_llm,
        trace_id=trace_id,
        run_id=body.run_id or f"run-{trace_id}",
        workflow="disruption",
        source="http",
    )


@app.post("/v1/datasets")
def datasets_seed():
    return seed_golden()


@app.get("/v1/datasets")
def datasets_list():
    return {"datasets": list_datasets()}


@app.get("/v1/datasets/{dataset_id}/versions")
def dataset_versions(dataset_id: str):
    rows = [d for d in list_datasets() if d["dataset_id"] == dataset_id]
    return {"versions": rows}


@app.post("/v1/experiments")
def experiments_launch(body: ExperimentIn):
    return run_experiment(body.name, body.version_id, judge_llm=body.judge_llm)


@app.get("/v1/experiments/{experiment_id}")
def experiments_get(experiment_id: str):
    found = get_experiment(experiment_id)
    if not found:
        raise HTTPException(404, "Unknown experiment")
    return found


@app.get("/v1/experiments/compare")
def experiments_compare(a: str, b: str):
    return compare(a, b)


@app.post("/v1/gates/check")
def gates_check(judge_llm: bool = False, execute: bool = False):
    return check_gate(judge_llm=judge_llm, execute=execute)


@app.get("/v1/annotations/queue")
def annotations_queue():
    return {"queue": queue()}


@app.post("/v1/annotations")
def annotations_post(body: AnnotationIn):
    return {"annotation_id": add_annotation(body.run_id, body.annotator, body.labels, body.rubric)}


@app.post("/v1/feedback")
def feedback_post(body: FeedbackIn):
    return {"feedback_id": add_feedback(body.run_id, body.source, body.rating, body.comment)}


@app.get("/v1/metrics/quality")
def metrics_quality():
    return {"quality": quality(), "alerts": maybe_alert()}


@app.get("/", response_class=HTMLResponse)
@app.get("/explorer", response_class=HTMLResponse)
def explorer():
    html = _STATIC / "explorer.html"
    return HTMLResponse(html.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


def main() -> None:
    import uvicorn

    apply_schema()
    seed_golden()
    print(f"Evaluation platform on http://{settings.host}:{settings.port}")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
