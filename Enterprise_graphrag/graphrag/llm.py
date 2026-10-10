"""All LLM calls go through the AI Gateway. Never a provider SDK."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from graphrag.config import settings
from graphrag.trace import extra_headers


def chat(system: str, user: str, *, temperature: float = 0, max_tokens: int = 1200) -> str | None:
    if os.getenv("GRAPHRAG_EXTRACT_ONLY", "").lower() == "true":
        return None
    body = {
        "model": os.getenv("GRAPHRAG_EXTRACT_MODEL", "gemini-3.5-flash-lite"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "X-Tenant-Id": settings.tenant_id,
        "X-Route-Hint": "cheap",
        **extra_headers(),
    }
    if settings.gateway_api_key:
        headers["Authorization"] = f"Bearer {settings.gateway_api_key}"
    req = urllib.request.Request(
        f"{settings.gateway_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
        return ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, OSError):
        return None
