"""HTTP read API — Digital Twin. Port 8003. Read-only."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from twin.db import fetch_one
from twin.mcp import call_tool as mcp_call, handle_jsonrpc, list_tools as mcp_list
from twin.queries import days_of_cover, news_signals, open_pos, plants_if_abc_down, vendor_material, vendor_materials

_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Supply Chain Digital Twin",
    version="0.1.0",
    description="Read API for shared SCM world state (Wave 1 disruption pack).",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = _STATIC / "index.html"
    if html.is_file():
        return HTMLResponse(html.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})
    return HTMLResponse("<p>Twin UI missing.</p>", status_code=503)


@app.get("/health")
def health() -> dict:
    row = fetch_one("SELECT COUNT(*) AS n FROM plants")
    return {"ok": True, "plants": int((row or {}).get("n") or 0)}


@app.get("/v1/twin/inventory/days-of-cover")
def inventory_days_of_cover(
    material: str = Query("MAT-SEMI-1", description="Material ID"),
) -> dict:
    rows = days_of_cover(material)
    return {"material_id": material, "positions": rows}


@app.get("/v1/twin/pos/open")
def pos_open(
    vendor: str | None = Query(None, description="Filter by vendor_id, e.g. VEND-ABC"),
) -> dict:
    rows = open_pos(vendor_id=vendor)
    return {"vendor_id": vendor, "purchase_orders": rows, "count": len(rows)}


@app.get("/v1/twin/signals/news")
def signals_news(
    entity: str | None = Query(None, description="Filter by entity_id, e.g. VEND-ABC"),
) -> dict:
    rows = news_signals(entity_id=entity)
    return {"signals": rows, "count": len(rows)}


@app.get("/v1/twin/plants-at-risk")
def plants_at_risk() -> dict:
    """SQL answer: which plants if VEND-ABC is disrupted."""
    return {"vendor_id": "VEND-ABC", "material_id": "MAT-SEMI-1", "plants": plants_if_abc_down()}


@app.get("/v1/twin/vendor-materials")
def vendor_materials_api(
    material: str = Query("MAT-SEMI-1"),
    vendor: str | None = Query(None),
) -> dict:
    if vendor:
        row = vendor_material(vendor, material)
        return {"material_id": material, "vendor_id": vendor, "rows": [row] if row else []}
    return {"material_id": material, "rows": vendor_materials(material)}


class McpCallIn(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


@app.get("/v1/mcp/tools")
def mcp_tools() -> dict:
    return {"tools": mcp_list()}


@app.post("/v1/mcp/tools/call")
def mcp_tools_call(body: McpCallIn) -> dict:
    try:
        result = mcp_call(body.name, body.arguments)
    except KeyError as exc:
        return {"error": True, "detail": str(exc)}
    return {"name": body.name, "result": result}


@app.post("/mcp")
def mcp_jsonrpc(body: dict) -> dict:
    return handle_jsonrpc(body)


def main() -> None:
    import uvicorn

    from twin.config import settings

    uvicorn.run("twin.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
