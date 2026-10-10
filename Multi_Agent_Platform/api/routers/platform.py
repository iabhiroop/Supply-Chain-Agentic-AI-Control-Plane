"""Platform control plane: chat, events, incident inspection."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from agents.chat.lookup import incident_snapshot
from agents.chat.models import ChatTurnRequest, ChatTurnResponse
from agents.chat.service import handle_turn
from agents.chat.store import get_session, list_messages
from agents.contracts import IncidentEvent
from agents.runtime import execute_workflow, open_incident, run_incident

router = APIRouter(tags=["Platform"])

_HTML_DIR = Path(__file__).resolve().parents[1] / "static"
_NO_STORE = {"Cache-Control": "no-store"}


def _html(name: str) -> HTMLResponse:
    return HTMLResponse((_HTML_DIR / name).read_text(encoding="utf-8"), headers=_NO_STORE)


class EventIngestRequest(IncidentEvent):
    wait_for_workflow: bool = Field(
        default=False,
        description="If true, block until the orchestrator finishes (slow; for tests).",
    )


class EventIngestResponse(BaseModel):
    incident_id: str
    created: bool
    status: str
    summary: str | None = None


@router.post("/v1/chat", response_model=ChatTurnResponse)
async def chat_turn(req: ChatTurnRequest):
    """Front door for operators. Confirmed events enter the existing incident runtime."""
    return await handle_turn(req)


@router.get("/v1/chat/sessions/{session_id}")
def chat_session(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session {session_id}")
    messages = list_messages(session_id)
    return {
        "session": {
            "session_id": str(session["session_id"]),
            "tenant_id": session["tenant_id"],
            "user_id": session["user_id"],
            "channel": session["channel"],
            "incident_id": str(session["incident_id"]) if session.get("incident_id") else None,
            "pending_proposal": session.get("pending_proposal"),
        },
        "messages": [
            {
                "message_id": m["message_id"],
                "role": m["role"],
                "intent": m["intent"],
                "content": m["content"],
                "cards": m["cards"],
                "created_at": m["created_at"].isoformat(sep=" ", timespec="seconds")
                if m.get("created_at") else None,
            }
            for m in messages
        ],
    }


@router.get("/v1/incidents/{incident_id}")
def get_incident(incident_id: str):
    snap = incident_snapshot(incident_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Unknown incident {incident_id}")
    return snap


@router.post("/v1/events", response_model=EventIngestResponse)
async def ingest_event(req: EventIngestRequest):
    """Typed event ingest used by chat confirm and by scripts/webhooks."""
    event = IncidentEvent.model_validate(req.model_dump(exclude={"wait_for_workflow"}))
    if req.wait_for_workflow:
        result = await run_incident(event, verbose=False)
    else:
        incident_id, created = open_incident(event)
        if not created:
            result = {"incident_id": incident_id, "created": False, "status": "DUPLICATE"}
        else:
            asyncio.create_task(execute_workflow(incident_id, event, verbose=False))
            result = {
                "incident_id": incident_id,
                "created": True,
                "status": "IN_PROGRESS",
                "summary": "Workflow started.",
            }
    return EventIngestResponse(
        incident_id=result["incident_id"],
        created=bool(result.get("created")),
        status=result.get("status", "UNKNOWN"),
        summary=result.get("summary"),
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_ui():
    return _html("console/index.html")


@router.get("/console/console.css", include_in_schema=False)
def console_css():
    return FileResponse(_HTML_DIR / "console" / "console.css", media_type="text/css", headers=_NO_STORE)


@router.get("/console/console.js", include_in_schema=False)
def console_js():
    return FileResponse(
        _HTML_DIR / "console" / "console.js",
        media_type="text/javascript",
        headers=_NO_STORE,
    )


@router.get("/chat", include_in_schema=False)
def chat_ui():
    """Legacy chat URL — copilot now lives as a side panel on the dashboard."""
    return RedirectResponse("/dashboard?copilot=1", status_code=307)
