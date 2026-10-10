"""Anthropic Messages API adapter (native)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from gateway.config import get_settings
from gateway.models.request import ChatCompletionRequest, ChatMessage, GatewayError, TokenUsage
from gateway.providers.base import BaseProvider, ProviderResponse
from gateway.providers.json_mode import json_mode_instruction

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, config: dict | None = None) -> None:
        cfg = config or {}
        super().__init__(cfg)
        settings = get_settings()
        self._api_key = api_key or cfg.get("api_key") or getattr(settings, "anthropic_api_key", "") or ""
        self._base_url = (base_url or cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
        self._timeout = 120.0
        self._version = "2023-06-01"

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def set_credentials(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        super().set_credentials(api_key=api_key, base_url=base_url)
        if api_key:
            self._api_key = api_key
        if base_url:
            self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
            "content-type": "application/json",
        }

    def _payload(self, request: ChatCompletionRequest) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for msg in request.messages:
            if msg.role == "system":
                text = msg.content if isinstance(msg.content, str) else str(msg.content or "")
                system_parts.append(text)
                continue
            messages.append(self._to_message(msg))
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or 1024,
            "messages": messages or [{"role": "user", "content": " "}],
        }
        if system_parts:
            body["system"] = "\n".join(system_parts)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.stop is not None:
            body["stop_sequences"] = request.stop if isinstance(request.stop, list) else [request.stop]
        if request.tools:
            body["tools"] = self._translate_tools(request.tools)
        extra = json_mode_instruction(request.response_format)
        if extra:
            body["system"] = f"{body.get('system', '')}\n\n{extra}".strip()
        return body

    @staticmethod
    def _to_message(msg: ChatMessage) -> dict[str, Any]:
        role = "assistant" if msg.role == "assistant" else "user"
        content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
        if msg.tool_call_id:
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": content,
                }],
            }
        if msg.tool_calls:
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in msg.tool_calls:
                fn = tc.get("function") or {}
                args = fn.get("arguments") or "{}"
                if isinstance(args, str):
                    import json
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or "tool",
                    "name": fn.get("name", ""),
                    "input": args,
                })
            return {"role": "assistant", "content": blocks}
        return {"role": role, "content": content}

    @staticmethod
    def _translate_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            fn = tool.get("function") or {}
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    async def complete(self, request: ChatCompletionRequest) -> ProviderResponse:
        url = f"{self._base_url}/v1/messages"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json=self._payload(request))
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise self._gateway_error(code="timeout", message="Anthropic request timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise self._map_http(exc) from exc
            except httpx.RequestError as exc:
                self._available = False
                raise self._gateway_error(code="provider_unavailable", message=str(exc)) from exc
        return self._parse(resp.json(), request.model)

    async def stream_text(self, request: ChatCompletionRequest):
        url = f"{self._base_url}/v1/messages"
        body = self._payload(request)
        body["stream"] = True
        text_parts: list[str] = []
        usage = TokenUsage()
        finish = "stop"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream("POST", url, headers=self._headers(), json=body) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        kind = data.get("type")
                        if kind == "content_block_delta":
                            piece = ((data.get("delta") or {}).get("text")) or ""
                            if piece:
                                text_parts.append(piece)
                                yield piece
                        elif kind == "message_delta":
                            usage_raw = data.get("usage") or {}
                            if usage_raw:
                                usage = TokenUsage(
                                    prompt_tokens=usage.prompt_tokens or usage_raw.get("input_tokens") or 0,
                                    completion_tokens=usage_raw.get("output_tokens") or 0,
                                    total_tokens=(usage.prompt_tokens or 0) + (usage_raw.get("output_tokens") or 0),
                                )
                            stop = (data.get("delta") or {}).get("stop_reason")
                            if stop:
                                finish = "tool_calls" if stop == "tool_use" else stop
                        elif kind == "message_start":
                            msg = data.get("message") or {}
                            usage_raw = msg.get("usage") or {}
                            usage = TokenUsage(
                                prompt_tokens=usage_raw.get("input_tokens") or 0,
                                completion_tokens=usage_raw.get("output_tokens") or 0,
                                total_tokens=(usage_raw.get("input_tokens") or 0) + (usage_raw.get("output_tokens") or 0),
                            )
            except httpx.TimeoutException as exc:
                raise self._gateway_error(code="timeout", message="Anthropic request timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise self._map_http(exc) from exc
            except httpx.RequestError as exc:
                raise self._gateway_error(code="provider_unavailable", message=str(exc)) from exc
        self.last_stream_result = ProviderResponse(
            content="".join(text_parts),
            usage=usage,
            finish_reason=finish,
            provider_name=self.provider_name,
            model=request.model,
        )

    async def health_check(self) -> bool:
        url = f"{self._base_url}/v1/models"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=self._headers())
                healthy = resp.status_code < 500
        except httpx.RequestError:
            healthy = False
        self._available = healthy
        return healthy

    def _parse(self, data: dict[str, Any], model: str) -> ProviderResponse:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                import json
                tool_calls.append({
                    "id": block.get("id") or "tool",
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                })
        usage_raw = data.get("usage") or {}
        prompt = usage_raw.get("input_tokens") or 0
        completion = usage_raw.get("output_tokens") or 0
        stop = data.get("stop_reason") or "stop"
        if stop == "tool_use":
            stop = "tool_calls"
        return ProviderResponse(
            content="\n".join(text_parts),
            role="assistant",
            tool_calls=tool_calls or None,
            finish_reason=stop,
            usage=TokenUsage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            ),
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
            400: ("bad_request", "Anthropic rejected the request"),
            401: ("auth_failed", "Anthropic API key is invalid"),
            403: ("auth_failed", "Anthropic API key lacks permission"),
            404: ("model_not_found", "Anthropic model not found"),
            429: ("rate_limited", "Anthropic rate limit exceeded"),
        }
        code, msg = mapping.get(status, ("provider_error", f"Anthropic HTTP {status}"))
        if status >= 500:
            code, msg = "provider_unavailable", f"Anthropic returned {status}"
        return self._gateway_error(
            code=code,
            message=msg,
            details={"status": status, "body": exc.response.text[:500]},
        )
