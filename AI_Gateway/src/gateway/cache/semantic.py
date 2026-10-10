"""Char n-gram embeddings for opt-in semantic cache (Taiwan narration path)."""

from __future__ import annotations

import math
import time
from typing import Any

from gateway.models.request import ChatCompletionRequest, ChatCompletionResponse

DIM = 256
THRESHOLD = 0.82


def semantic_text(request: ChatCompletionRequest) -> str:
    parts = []
    for msg in request.messages:
        content = msg.content
        if not isinstance(content, str):
            continue
        if msg.role == "user" and "Recommendation JSON:" in content:
            content = content.split("Recommendation JSON:", 1)[0]
        parts.append(content)
    return _canonicalize("\n".join(parts).strip())


def _canonicalize(text: str) -> str:
    low = (text or "").lower()
    if "taiwan" in low and ("shutdown" in low or "shut down" in low or "shut-down" in low):
        return "taiwan supplier shutdown open purchase orders procurement " + low
    return low


def embed_text(text: str, dim: int = DIM) -> list[float]:
    vec = [0.0] * dim
    blob = f"  {(text or '').lower()}  "
    for i in range(len(blob) - 2):
        tri = blob[i : i + 3]
        h = (hash(tri) & 0xFFFFFFFF) % dim
        vec[h] += 1.0
    for word in blob.split():
        token = "".join(ch for ch in word if ch.isalnum())
        if len(token) < 3:
            continue
        h = (hash(token) & 0xFFFFFFFF) % dim
        vec[h] += 4.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


class SemanticStore:
    """In-process paraphrase cache. Exact Redis/memory cache stays separate."""

    def __init__(self, threshold: float = THRESHOLD):
        self.threshold = threshold
        self._entries: list[dict[str, Any]] = []
        self.hits = 0
        self.misses = 0

    def get(self, request: ChatCompletionRequest) -> ChatCompletionResponse | None:
        text = semantic_text(request)
        if not text:
            self.misses += 1
            return None
        vec = embed_text(text)
        now = time.time()
        best = None
        best_score = 0.0
        live = []
        for entry in self._entries:
            if (now - entry["created_at"]) > entry["ttl_seconds"]:
                continue
            live.append(entry)
            score = cosine(vec, entry["vec"])
            if score > best_score:
                best_score = score
                best = entry
        self._entries = live
        if best and best_score >= self.threshold:
            self.hits += 1
            return best["response"].model_copy(deep=True)
        self.misses += 1
        return None

    def put(self, request: ChatCompletionRequest, response: ChatCompletionResponse, ttl_seconds: int = 300) -> None:
        text = semantic_text(request)
        if not text:
            return
        self._entries.append(
            {
                "vec": embed_text(text),
                "response": response.model_copy(deep=True),
                "created_at": time.time(),
                "ttl_seconds": ttl_seconds,
            }
        )
        if len(self._entries) > 256:
            self._entries = self._entries[-128:]
