"""Process-wide trace_id for one execute_transaction / decide call."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("scm_trace_id", default=None)


def current_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(trace_id: str | None):
    return _trace_id.set(trace_id)


def reset_trace_id(token) -> None:
    _trace_id.reset(token)


def mint_trace_id(transaction_id: str) -> str:
    return f"l2-{transaction_id}-{uuid.uuid4().hex[:8]}"


def headers() -> dict[str, str]:
    tid = current_trace_id()
    return {"X-Trace-Id": tid} if tid else {}
