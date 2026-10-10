"""
OpenAI provider — native Chat Completions adapter (httpx).

Also works against OpenAI-compatible hosts (Groq, Together, Azure) when base_url is set.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from gateway.config import get_settings
from gateway.models.request import ChatCompletionRequest, GatewayError, TokenUsage
from gateway.providers.base import BaseProvider, ProviderResponse

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, config: dict | None = None) -> None:
        cfg = config or {}
        super().__init__(cfg)
        settings = get_settings()
        self._api_key = api_key or cfg.get("api_key") or getattr(settings, "openai_api_key", "") or ""
        self._base_url = (base_url or cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self._timeout = 120.0

    @property
    def provider_name(self) -> str:
        return "openai"

    def set_credentials(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        super().set_credentials(api_key=api_key, base_url=base_url)
        if api_key:
            self._api_key = api_key
        if base_url:
            self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: ChatCompletionRequest, stream: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [m.model_dump(exclude_none=True) for m in request.messages],
            "stream": stream,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.tools:
            body["tools"] = request.tools
        if request.tool_choice is not None:
            body["tool_choice"] = request.tool_choice
        if request.stop is not None:
            body["stop"] = request.stop
        if request.response_format is not None:
            body["response_format"] = request.response_format
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    async def complete(self, request: ChatCompletionRequest) -> ProviderResponse:
        url = f"{self._base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json=self._payload(request))
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise self._gateway_error(code="timeout", message="OpenAI request timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise self._map_http(exc) from exc
            except httpx.RequestError as exc:
                self._available = False
                raise self._gateway_error(code="provider_unavailable", message=str(exc)) from exc
        data = resp.json()
        return self._parse(data, request.model)

    async def stream_text(self, request: ChatCompletionRequest):
        url = f"{self._base_url}/chat/completions"
        text_parts: list[str] = []
        usage = TokenUsage()
        finish = "stop"
        tool_calls = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream(
                    "POST", url, headers=self._headers(), json=self._payload(request, stream=True)
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if data.get("usage"):
                            usage_raw = data["usage"]
                            usage = TokenUsage(
                                prompt_tokens=usage_raw.get("prompt_tokens") or 0,
                                completion_tokens=usage_raw.get("completion_tokens") or 0,
                                total_tokens=usage_raw.get("total_tokens") or 0,
                            )
                        choice = (data.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            text_parts.append(piece)
                            yield piece
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
                        if delta.get("tool_calls"):
                            tool_calls = delta.get("tool_calls")
            except httpx.TimeoutException as exc:
                raise self._gateway_error(code="timeout", message="OpenAI request timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise self._map_http(exc) from exc
            except httpx.RequestError as exc:
                raise self._gateway_error(code="provider_unavailable", message=str(exc)) from exc
        self.last_stream_result = ProviderResponse(
            content="".join(text_parts),
            role="assistant",
            tool_calls=tool_calls,
            finish_reason=finish,
            usage=usage,
            provider_name=self.provider_name,
            model=request.model,
        )

    async def health_check(self) -> bool:
        url = f"{self._base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=self._headers())
                healthy = resp.status_code < 500
        except httpx.RequestError:
            healthy = False
        self._available = healthy
        return healthy

    def _parse(self, data: dict[str, Any], model: str) -> ProviderResponse:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage_raw = data.get("usage") or {}
        usage = TokenUsage(
            prompt_tokens=usage_raw.get("prompt_tokens") or 0,
            completion_tokens=usage_raw.get("completion_tokens") or 0,
            total_tokens=usage_raw.get("total_tokens") or 0,
        )
        return ProviderResponse(
            content=message.get("content") or "",
            role=message.get("role") or "assistant",
            tool_calls=message.get("tool_calls"),
            finish_reason=choice.get("finish_reason") or "stop",
            usage=usage,
            raw_response=data,
            provider_name=self.provider_name,
            model=data.get("model") or model,
        )

    @staticmethod
    def _gateway_error(*, code: str, message: str, details: dict | None = None) -> Exception:
        err = GatewayError(error="provider_error", code=code, message=message, details=details)
        return ValueError(err.model_dump_json())

    def _map_http(self, exc: httpx.HTTPStatusError) -> Exception:
        status = exc.response.status_code
        mapping = {
            400: ("bad_request", "OpenAI rejected the request"),
            401: ("auth_failed", "OpenAI API key is invalid"),
            403: ("auth_failed", "OpenAI API key lacks permission"),
            404: ("model_not_found", "OpenAI model not found"),
            429: ("rate_limited", "OpenAI rate limit exceeded"),
        }
        code, msg = mapping.get(status, ("provider_error", f"OpenAI HTTP {status}"))
        if status >= 500:
            code, msg = "provider_unavailable", f"OpenAI returned {status}"
        body = exc.response.text[:500]
        return self._gateway_error(code=code, message=msg, details={"status": status, "body": body})
