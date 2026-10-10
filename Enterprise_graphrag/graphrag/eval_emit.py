"""Fire-and-forget retrieval traces to Layer 4. Never block answering."""

from __future__ import annotations

import json
import os
import uuid
import urllib.error
import urllib.request
from typing import Any

from graphrag.config import settings
from graphrag.trace import current_trace_id


def _parent_workflow_span(trace_id: str | None) -> str | None:
    """Layer 2 mints l2-{transaction_id}-{8hex}; workflow span is wf-{transaction_id}."""
    if not trace_id or not trace_id.startswith("l2-"):
        return None
    body = trace_id[3:]
    prefix, sep, suffix = body.rpartition("-")
    if sep and len(suffix) == 8 and prefix:
        return f"wf-{prefix}"
    return None


def emit_retrieval(kind: str, query: str, payload: dict[str, Any]) -> None:
    if os.getenv("SCM_EVAL_EMIT", "true").lower() != "true":
        return
    retrieval = payload.get("retrieval") or payload
    parent = current_trace_id()
    trace_id = parent or f"l3-{kind}-{uuid.uuid4().hex[:10]}"
    span_id = f"l3-{kind}-{uuid.uuid4().hex[:12]}"
    trace = {
        "trace_id": trace_id,
        "tenant_id": settings.tenant_id,
        "workflow": "p2p_execution" if parent else "graphrag_retrieve",
        "source": "graphrag",
        "metadata": {"kind": kind, "intent": retrieval.get("intent")},
        "spans": [
            {
                "span_id": span_id,
                "parent_span_id": _parent_workflow_span(parent),
                "name": f"graphrag.{kind}",
                "kind": "retrieval",
                "status": "OK",
                "input": {"query": query},
                "output": {
                    "strategies": retrieval.get("strategies"),
                    "n_passages": len(retrieval.get("passages") or payload.get("citations") or []),
                    "abstained": payload.get("abstained"),
                    "confidence": payload.get("confidence"),
                },
            }
        ],
    }
    req = urllib.request.Request(
        settings.eval_url.rstrip("/") + "/v1/traces",
        data=json.dumps(trace, default=str).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=2)
    except (urllib.error.URLError, TimeoutError, OSError):
        return
