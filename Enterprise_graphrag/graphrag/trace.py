"""Incoming X-Trace-Id from Layer 2, forwarded to the gateway."""

from __future__ import annotations

from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("graphrag_trace_id", default=None)


def current_trace_id() -> str | None:
    return _trace_id.get()


def bind_trace_id(trace_id: str | None):
    return _trace_id.set(trace_id)


def reset_trace_id(token) -> None:
    _trace_id.reset(token)


def extra_headers() -> dict[str, str]:
    tid = current_trace_id()
    return {"X-Trace-Id": tid} if tid else {}
