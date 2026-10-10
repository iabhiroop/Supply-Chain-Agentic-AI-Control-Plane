"""PII redaction on ingest."""

from __future__ import annotations

import re
from typing import Any

EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE = re.compile(r"\b\+?\d[\d\-\s]{8,}\d\b")


def redact(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return PHONE.sub("[PHONE]", EMAIL.sub("[EMAIL]", value))
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
