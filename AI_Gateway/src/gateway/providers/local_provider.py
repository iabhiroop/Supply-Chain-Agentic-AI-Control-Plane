"""
Local Provider — Ollama REST API adapter.

Translates gateway ChatCompletionRequest (OpenAI format) into the Ollama
``/api/chat`` format and normalises responses back into a ``ProviderResponse``.

Configuration is read directly from the environment via GatewaySettings:
  GATEWAY_OLLAMA_BASE_URL — base URL of the running Ollama instance
                            (default: http://host.docker.internal:11434)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from gateway.config import get_settings
from gateway.models.request import (
    ChatCompletionRequest,
    ChatMessage,
    GatewayError,
    TokenUsage,
)
from gateway.providers.base import BaseProvider, ProviderResponse

logger = logging.getLogger(__name__)


class LocalProvider(BaseProvider):
    """Adapter for the Ollama local inference server.

    Reads GATEWAY_OLLAMA_BASE_URL from the environment (via GatewaySettings).
    Uses httpx for async HTTP calls to the Ollama /api/chat endpoint.
    """

    def __init__(self, base_url: str | None = None, config: dict | None = None) -> None:
        cfg = config or {}
        super().__init__(cfg)
        settings = get_settings()
        self._base_url: str = (base_url or cfg.get("base_url") or settings.ollama_base_url).rstrip("/")
        self._timeout: float = 300

    def set_credentials(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        super().set_credentials(api_key=api_key, base_url=base_url)
        if base_url:
            self._base_url = base_url.rstrip("/")

    # -- Public interface -----------------------------------------------------

    @property
    def provider_name(self) -> str:  # noqa: D401
        return "ollama"

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Call Ollama ``POST /api/embed`` (embeddinggemma or any pulled embed model)."""
        url = f"{self._base_url}/api/embed"
        payload = {"model": model, "input": texts}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                raise self._gateway_error(
                    code="provider_timeout",
                    message=f"Ollama embed timed out after {self._timeout}s",
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise self._map_http_error(exc) from exc
            except httpx.RequestError as exc:
                raise self._gateway_error(
                    code="provider_unavailable",
                    message=f"Cannot reach Ollama at {self._base_url}: {exc}",
                ) from exc
        data = resp.json()
        vectors = data.get("embeddings") or []
        if not vectors and data.get("embedding"):
            vectors = [data["embedding"]]
        if len(vectors) != len(texts):
            raise self._gateway_error(
                code="provider_error",
                message=f"Ollama returned {len(vectors)} vectors for {len(texts)} inputs",
            )
        return vectors

    async def complete(self, request: ChatCompletionRequest) -> ProviderResponse:
        """Translate *request* into an Ollama ``/api/chat`` call."""
        url = f"{self._base_url}/api/chat"
        payload = self._build_payload(request)
        logger.debug("Ollama request to %s: %s", request.model, payload)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                logger.error("Ollama timeout for model %s: %s", request.model, exc)
                self._available = False
                raise self._gateway_error(
                    code="provider_timeout",
                    message=f"Ollama request timed out after {self._timeout}s",
                ) from exc
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "Ollama HTTP %s for model %s: %s",
                    exc.response.status_code, request.model, exc.response.text,
                )
                raise self._map_http_error(exc) from exc
            except httpx.RequestError as exc:
                logger.error("Ollama connection error: %s", exc)
                self._available = False
                raise self._gateway_error(
                    code="provider_unavailable",
                    message=f"Cannot reach Ollama at {self._base_url}: {exc}",
                ) from exc

        data: dict[str, Any] = resp.json()
        return self._parse_response(data, model=request.model)

    async def stream_text(self, request: ChatCompletionRequest):
        url = f"{self._base_url}/api/chat"
        payload = self._build_payload(request)
        payload["stream"] = True
        text_parts: list[str] = []
        last_data: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        last_data = data
                        piece = ((data.get("message") or {}).get("content")) or ""
                        if piece:
                            text_parts.append(piece)
                            yield piece
            except httpx.TimeoutException as exc:
                raise self._gateway_error(
                    code="provider_timeout",
                    message=f"Ollama request timed out after {self._timeout}s",
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise self._map_http_error(exc) from exc
            except httpx.RequestError as exc:
                raise self._gateway_error(
                    code="provider_unavailable",
                    message=f"Cannot reach Ollama at {self._base_url}: {exc}",
                ) from exc
        parsed = self._parse_response(last_data or {"message": {"content": "".join(text_parts)}}, model=request.model)
        if not parsed.content:
            parsed = parsed.model_copy(update={"content": "".join(text_parts)})
        self.last_stream_result = parsed

    async def health_check(self) -> bool:
        """Hit ``/api/tags`` to check Ollama is running and has models."""
        url = f"{self._base_url}/api/tags"
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                resp = await client.get(url)
                healthy = resp.status_code == 200
            except httpx.RequestError:
                healthy = False
        self._available = healthy
        logger.info("Ollama health check: %s", "OK" if healthy else "FAIL")
        return healthy

    # -- Request translation --------------------------------------------------

    def _build_payload(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """Convert an OpenAI-style request into Ollama JSON body."""
        payload: dict[str, Any] = {
            "model": request.model,
            "stream": False,
        }

        # --- messages --------------------------------------------------------
        messages: list[dict[str, Any]] = []
        for msg in request.messages:
            messages.append(self._message_to_dict(msg))
        payload["messages"] = messages

        # --- options ---------------------------------------------------------
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if request.top_p is not None:
            options["top_p"] = request.top_p
        if request.stop is not None:
            stop = request.stop if isinstance(request.stop, list) else [request.stop]
            options["stop"] = stop
        if options:
            payload["options"] = options

        # --- tools (function calling, model-dependent) -----------------------
        if request.tools:
            payload["tools"] = self._translate_tools(request.tools)

        from gateway.providers.json_mode import json_mode_instruction
        extra = json_mode_instruction(request.response_format)
        if extra:
            payload["format"] = "json"
            messages = payload.get("messages") or []
            messages.insert(0, {"role": "system", "content": extra})
            payload["messages"] = messages

        return payload

    @staticmethod
    def _message_to_dict(msg: ChatMessage) -> dict[str, Any]:
        """Convert a ``ChatMessage`` to an Ollama message dict."""
        out: dict[str, Any] = {"role": msg.role}

        # Content — Ollama expects a string
        if msg.content is not None:
            out["content"] = msg.content if isinstance(msg.content, str) else str(msg.content)
        else:
            out["content"] = ""

        # Tool-call results
        if msg.tool_call_id:
            out["tool_call_id"] = msg.tool_call_id

        # Outbound tool calls from assistant
        if msg.tool_calls:
            out["tool_calls"] = msg.tool_calls

        return out

    @staticmethod
    def _translate_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pass OpenAI-style tool definitions through to Ollama.

        Ollama's tool format closely mirrors OpenAI's, so we keep the
        structure as-is and only ensure each entry has the expected shape.
        """
        translated: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            fn = tool.get("function", {})
            translated.append({
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                },
            })
        return translated

    # -- Response parsing -----------------------------------------------------

    def _parse_response(self, data: dict[str, Any], *, model: str) -> ProviderResponse:
        """Parse an Ollama ``/api/chat`` response into ``ProviderResponse``."""
        message = data.get("message", {})
        content: str = message.get("content", "")
        role: str = message.get("role", "assistant")

        # --- Tool calls ------------------------------------------------------
        raw_tool_calls = message.get("tool_calls")
        tool_calls: list[dict[str, Any]] | None = None
        if raw_tool_calls:
            tool_calls = []
            for idx, tc in enumerate(raw_tool_calls):
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, dict):
                    args = json.dumps(args)
                tool_calls.append({
                    "id": f"call_{idx}",
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": args,
                    },
                })

        # --- Finish reason ---------------------------------------------------
        finish_reason = "tool_calls" if tool_calls else "stop"
        if data.get("done_reason"):
            finish_reason = data["done_reason"]

        # --- Token usage (Ollama field names) --------------------------------
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        return ProviderResponse(
            content=content,
            role=role,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            raw_response=data,
            provider_name=self.provider_name,
            model=model,
        )

    # -- Error helpers --------------------------------------------------------

    @staticmethod
    def _gateway_error(*, code: str, message: str) -> Exception:
        """Build a ``ValueError`` wrapping a ``GatewayError`` payload."""
        err = GatewayError(error="provider_error", code=code, message=message)
        return ValueError(err.model_dump_json())

    @staticmethod
    def _map_http_error(exc: httpx.HTTPStatusError) -> Exception:
        """Map Ollama HTTP errors to gateway error codes."""
        status = exc.response.status_code
        match status:
            case 400:
                code, msg = "bad_request", "Ollama rejected the request"
            case 404:
                code, msg = "model_not_found", "Model not pulled in Ollama"
            case _ if status >= 500:
                code, msg = "provider_server_error", f"Ollama returned {status}"
            case _:
                code, msg = "provider_error", f"Ollama HTTP {status}"

        detail = exc.response.text[:500]
        err = GatewayError(
            error="provider_error",
            code=code,
            message=msg,
            details={"status": status, "body": detail},
        )
        return ValueError(err.model_dump_json())
