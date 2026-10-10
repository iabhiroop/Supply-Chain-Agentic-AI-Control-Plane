"""FastAPI BFF + static React shell. Port 8010. Proxies; does not orchestrate."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from os_platform import compose
from os_platform.config import settings
from os_platform.http import request

app = FastAPI(title="Enterprise AI Operating Platform", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_STATIC = Path(__file__).resolve().parent.parent / "web" / "dist"


class AskIn(BaseModel):
    question: str = Field(default=compose.TAIWAN_QUESTION)
    include_chat: bool = True
    include_graphrag: bool = True
    include_eval: bool = True


class P2PRunIn(BaseModel):
    transaction_id: str = Field(default=compose.PR1001)


class DecideIn(BaseModel):
    action: str = "approve_exception"
    wait: bool = True
    option: str | None = None
    note: str | None = None
    request_id: str | None = None


class ApplyIn(BaseModel):
    recommendation: dict = Field(default_factory=dict)
    decision: str = "approve"


class UndoIn(BaseModel):
    action_id: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "operating-platform", "port": settings.port}


@app.get("/v1/health/stack")
def health_stack():
    return compose.stack_health()


@app.post("/v1/ask")
def ask(
    body: AskIn,
    x_trace_id: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
):
    return compose.ask(
        question=body.question,
        tenant_id=x_tenant_id or settings.tenant_id,
        trace_id=x_trace_id,
        include_chat=body.include_chat,
        include_graphrag=body.include_graphrag,
        include_eval=body.include_eval,
    )


@app.get("/v1/runs/{trace_id}")
def get_run(trace_id: str):
    return {
        "trace_id": trace_id,
        "usage": compose.fetch_usage(trace_id, settings.tenant_id),
        "eval": compose.fetch_eval(trace_id),
    }


@app.post("/v1/p2p/run")
def p2p_run(
    body: P2PRunIn | None = None,
    x_trace_id: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
):
    payload = body or P2PRunIn()
    tenant = x_tenant_id or settings.tenant_id
    trace_id = x_trace_id or f"os-p2p-{payload.transaction_id[:8]}"
    return {
        "trace_id": trace_id,
        "tenant_id": tenant,
        **compose.run_p2p(payload.transaction_id, trace_id, tenant),
    }


@app.get("/v1/p2p/{transaction_id}")
def p2p_get(transaction_id: str):
    code, body = request(
        "GET",
        f"{settings.layer2_url.rstrip('/')}/v1/execution/transactions/{transaction_id}",
        timeout=8.0,
    )
    if code != 200:
        raise HTTPException(status_code=code or 502, detail=body)
    return body


@app.post("/v1/hitl/{transaction_id}/decide")
def hitl_decide(transaction_id: str, body: DecideIn | None = None):
    payload = body or DecideIn()
    code, result = request(
        "POST",
        f"{settings.layer2_url.rstrip('/')}/v1/execution/transactions/{transaction_id}/decide",
        body={"action": payload.action, "wait": payload.wait, "option": payload.option, "note": payload.note, "request_id": payload.request_id},
        timeout=180.0,
    )
    if code != 200:
        raise HTTPException(status_code=code or 502, detail=result)
    return result


@app.get("/v1/inbox")
def inbox():
    return compose.fetch_inbox()


@app.get("/v1/scorecard")
def scorecard(trace_id: str | None = None):
    card = compose.fetch_scorecard(trace_id)
    if card is None:
        return {"unavailable": True}
    return card


@app.get("/v1/budget")
def budget():
    return compose.fetch_budget() or {"unavailable": True}


@app.get("/v1/twin/snapshot")
def twin_snapshot():
    return compose.fetch_twin_snapshot()


@app.get("/v1/twin/plants-at-risk")
def twin_plants():
    return compose.twin_get("/v1/twin/plants-at-risk")


@app.get("/v1/twin/inventory/days-of-cover")
def twin_cover(material: str = "MAT-SEMI-1"):
    return compose.twin_get(f"/v1/twin/inventory/days-of-cover?material={material}")


@app.get("/v1/twin/pos/open")
def twin_pos(vendor: str | None = None):
    qs = f"?vendor={vendor}" if vendor else ""
    return compose.twin_get(f"/v1/twin/pos/open{qs}")


@app.get("/v1/twin/signals/news")
def twin_news(entity: str | None = None):
    qs = f"?entity={entity}" if entity else ""
    return compose.twin_get(f"/v1/twin/signals/news{qs}")


@app.post("/v1/disruption/apply")
def disruption_apply(body: ApplyIn | None = None):
    payload = body or ApplyIn()
    result = compose.apply_disruption(payload.recommendation, payload.decision)
    if result.get("error") and int(result.get("status_code") or 200) >= 400:
        raise HTTPException(status_code=int(result.get("status_code") or 502), detail=result)
    return result


@app.post("/v1/disruption/undo")
def disruption_undo(body: UndoIn):
    result = compose.undo_disruption(body.action_id)
    if result.get("error") and int(result.get("status_code") or 200) >= 400:
        raise HTTPException(status_code=int(result.get("status_code") or 502), detail=result)
    return result


def _index_file() -> Path | None:
    dist = _STATIC / "index.html"
    return dist if dist.is_file() else None


@app.get("/", response_class=HTMLResponse)
def index():
    found = _index_file()
    if not found:
        return HTMLResponse(
            "<p>Operating Platform UI not built. Run <code>npm install && npm run build</code> in web/.</p>",
            status_code=503,
        )
    return FileResponse(found)


_assets = _STATIC / "assets"
if _assets.is_dir():
    app.mount("/assets", StaticFiles(directory=_assets), name="assets")


def main() -> None:
    import uvicorn

    print(f"Operating Platform on http://{settings.host}:{settings.port}")
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
