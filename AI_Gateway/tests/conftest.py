"""Shared Gateway pytest harness: temp SQLite, no Redis, stub LLM providers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateway.models.request import TokenUsage
from gateway.providers.base import BaseProvider, ProviderResponse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class StubProvider(BaseProvider):
    """In-process LLM stand-in. Never opens a network socket."""

    def __init__(self, name: str, *, content: str = "stub-ok") -> None:
        super().__init__({})
        self._name = name
        self.content = content
        self.calls = 0
        self.fail_code: str | None = None
        self.vectors = [0.1, 0.2, 0.3, 0.4]

    @property
    def provider_name(self) -> str:
        return self._name

    async def complete(self, request) -> ProviderResponse:
        self.calls += 1
        if self.fail_code:
            raise ValueError(json.dumps({"code": self.fail_code, "error": self.fail_code}))
        text = self.content
        return ProviderResponse(
            content=text,
            usage=TokenUsage(
                prompt_tokens=4,
                completion_tokens=max(1, len(text.split())),
                total_tokens=4 + max(1, len(text.split())),
            ),
            provider_name=self._name,
            model=getattr(request, "model", "") or "",
        )

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        self.calls += 1
        return [list(self.vectors) for _ in texts]

    async def health_check(self) -> bool:
        return True


def _chat_payload(content: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"messages": [{"role": "user", "content": content}]}
    body.update(extra)
    return body


@pytest.fixture(scope="session")
def session_gateway(tmp_path_factory):
    db = tmp_path_factory.mktemp("gw") / "gateway-test.db"
    os.environ["GATEWAY_DATABASE_URL"] = f"sqlite+aiosqlite:///{db.as_posix()}"
    os.environ["GATEWAY_REDIS_URL"] = ""
    os.environ["GATEWAY_GEMINI_API_KEY"] = ""
    os.environ["GATEWAY_OPENAI_API_KEY"] = ""
    os.environ["GATEWAY_ANTHROPIC_API_KEY"] = ""
    os.environ["GATEWAY_DEBUG"] = "true"
    os.environ["GATEWAY_MODELS_CONFIG_PATH"] = str(ROOT / "config" / "models.yaml")
    os.environ["GATEWAY_ROUTES_CONFIG_PATH"] = str(ROOT / "config" / "routes.yaml")
    os.chdir(ROOT)

    from gateway import dependencies as deps
    from gateway.config import get_settings

    get_settings.cache_clear()
    deps._container = None

    from gateway.main import app

    gemini = StubProvider("gemini", content="gemini-stub")
    local = StubProvider("local", content="local-stub")
    with TestClient(app) as client:
        container = deps.get_container()
        container.providers["gemini"] = gemini
        container.providers["local"] = local
        yield {
            "client": client,
            "gemini": gemini,
            "local": local,
            "container": container,
        }

    deps._container = None
    get_settings.cache_clear()


@pytest.fixture
def gateway(session_gateway):
    from gateway.cache.exact_cache import ExactMatchCache
    from gateway.cache.layered import LayeredCache

    session_gateway["gemini"].calls = 0
    session_gateway["local"].calls = 0
    session_gateway["gemini"].fail_code = None
    session_gateway["local"].fail_code = None
    session_gateway["container"].cache = LayeredCache(ExactMatchCache())
    yield session_gateway


@pytest.fixture
def client(gateway):
    return gateway["client"]


@pytest.fixture
def do_chat():
    return chat


@pytest.fixture
def issue_key():
    return mint_key


def mint_key(client: TestClient, *, name: str = "pytest-key", rpm: int = 60, tpm: int = 100_000) -> str:
    res = client.post(
        "/v1/admin/keys",
        json={
            "name": name,
            "rate_limits": {"rpm": rpm, "tpm": tpm},
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["key"]


def chat(
    client: TestClient,
    content: str = "ping",
    *,
    headers: dict[str, str] | None = None,
    **body: Any,
):
    return client.post(
        "/v1/chat/completions",
        json=_chat_payload(content, **body),
        headers=headers or {},
    )
