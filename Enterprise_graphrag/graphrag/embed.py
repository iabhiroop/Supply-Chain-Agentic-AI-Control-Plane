"""Embeddings: Gateway `/v1/embeddings` first, then Ollama, then hashed fallback."""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request

from graphrag.config import settings


def _hash_vec(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for tok in text.lower().split():
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h // dim) % dim] -= 0.25
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _parse_ollama(payload: dict, n: int) -> list[list[float]] | None:
    vectors = payload.get("embeddings") or []
    if not vectors and payload.get("embedding"):
        vectors = [payload["embedding"]]
    if len(vectors) == n:
        return vectors
    return None


def _ollama_embed(texts: list[str]) -> list[list[float]] | None:
    url = f"{settings.ollama_url.rstrip('/')}/api/embed"
    body = json.dumps({"model": settings.embed_model, "input": texts}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        return _parse_ollama(payload, len(texts))
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, OSError):
        return None


def _gateway_embed(texts: list[str]) -> list[list[float]] | None:
    if not settings.gateway_api_key and "localhost" not in settings.gateway_url:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.gateway_api_key:
        headers["Authorization"] = f"Bearer {settings.gateway_api_key}"
        headers["X-Tenant-Id"] = settings.tenant_id
    from graphrag.trace import extra_headers
    headers.update(extra_headers())
    req = urllib.request.Request(
        f"{settings.gateway_url.rstrip('/')}/v1/embeddings",
        data=json.dumps({"model": settings.embed_model, "input": texts}).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
        data = payload.get("data") or []
        if data:
            return [row["embedding"] for row in sorted(data, key=lambda r: r.get("index", 0))]
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, OSError):
        return None
    return None


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    for fn in (_gateway_embed, _ollama_embed):
        got = fn(texts)
        if got:
            return got
    return [_hash_vec(t, settings.embed_dim) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))
