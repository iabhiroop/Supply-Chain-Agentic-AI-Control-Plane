"""
Gemini Provider — Google GenAI SDK adapter.

Translates gateway ChatCompletionRequest (OpenAI format) into the Gemini SDK
format and normalises responses back into a ``ProviderResponse``.

Configuration is read from GatewaySettings:
  GATEWAY_GEMINI_API_KEY — passed to the google-genai Client (no base URL)
"""

from __future__ import annotations

import logging
import json
import uuid
from collections import OrderedDict
from typing import Any

from google import genai
from google.genai.errors import APIError

from gateway.config import get_settings
from gateway.models.request import (
    ChatCompletionRequest,
    ChatMessage,
    GatewayError,
    TokenUsage,
)
from gateway.providers.base import BaseProvider, ProviderResponse

logger = logging.getLogger(__name__)

# Gemini uses "model" for the assistant role.
_ROLE_MAP: dict[str, str] = {
    "assistant": "model",
    "user": "user",
    "tool": "user",
}

_TOOL_CALL_ID_PREFIX = "call_"

# --- Thought-signature store -------------------------------------------------
# Gemini 3.x requires every replayed functionCall part to carry the
# thought_signature it was originally issued with, otherwise the API rejects the
# request. OpenAI's schema has nowhere to put that value, and clients such as
# langchain-openai discard unknown fields on tool calls, so it cannot survive a
# round trip through the caller.
#
# The gateway therefore remembers signatures itself, keyed by the tool_call_id
# it handed out, and re-attaches them when the caller replays the tool call.
# This keeps the OpenAI-compatible surface unchanged.
#
# In-process and bounded: adequate for a single replica. A multi-replica
# deployment should move this into Redis, since a replayed tool call may land on
# a different replica than the one that issued it.
_THOUGHT_SIGNATURE_CACHE: OrderedDict[str, Any] = OrderedDict()
_THOUGHT_SIGNATURE_CACHE_MAX = 4096


def _remember_thought_signature(tool_call_id: str, signature: Any) -> None:
    if not tool_call_id or signature is None:
        return
    _THOUGHT_SIGNATURE_CACHE[tool_call_id] = signature
    _THOUGHT_SIGNATURE_CACHE.move_to_end(tool_call_id)
    while len(_THOUGHT_SIGNATURE_CACHE) > _THOUGHT_SIGNATURE_CACHE_MAX:
        _THOUGHT_SIGNATURE_CACHE.popitem(last=False)


def _recall_thought_signature(tool_call_id: str | None) -> Any | None:
    if not tool_call_id:
        return None
    return _THOUGHT_SIGNATURE_CACHE.get(tool_call_id)


class GeminiProvider(BaseProvider):
    """Adapter for Google Gemini via the google-genai SDK.

    Reads GATEWAY_GEMINI_API_KEY from the environment (via GatewaySettings).
    The SDK client is created fresh per request inside an async context manager.
    """

    def __init__(self, api_key: str | None = None, config: dict | None = None) -> None:
        cfg = config or {}
        super().__init__(cfg)
        settings = get_settings()
        self._api_key: str = api_key or cfg.get("api_key") or settings.gemini_api_key
        self._timeout: float = 120

    def set_credentials(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        super().set_credentials(api_key=api_key, base_url=base_url)
        if api_key:
            self._api_key = api_key

    # -- Public interface -----------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def complete(self, request: ChatCompletionRequest) -> ProviderResponse:
        """Translate *request* into a Gemini SDK generate_content call."""
        contents, system_instruction = self._build_contents(request.messages)
        config = self._build_config(request, system_instruction)

        logger.debug("Gemini request to %s via GenAI SDK", request.model)

        try:
            async with genai.Client(api_key=self._api_key).aio as aclient:
                response = await aclient.models.generate_content(
                    model=request.model,
                    contents=contents,
                    config=config,
                )
        except APIError as exc:
            logger.error("Gemini API error for model %s: %s", request.model, exc)
            raise self._map_api_error(exc) from exc
        except Exception as exc:
            logger.error("Gemini connection error: %s", exc)
            self._available = False
            raise self._gateway_error(
                code="provider_error",
                message=f"Gemini error: {exc}",
            ) from exc

        return self._parse_response(response, model=request.model)

    async def stream_text(self, request: ChatCompletionRequest):
        contents, system_instruction = self._build_contents(request.messages)
        config = self._build_config(request, system_instruction)
        text_parts: list[str] = []
        last_chunk = None
        try:
            async with genai.Client(api_key=self._api_key).aio as aclient:
                stream = aclient.models.generate_content_stream(
                    model=request.model,
                    contents=contents,
                    config=config,
                )
                if hasattr(stream, "__await__"):
                    stream = await stream
                async for chunk in stream:
                    last_chunk = chunk
                    piece = getattr(chunk, "text", None) or ""
                    if piece:
                        text_parts.append(piece)
                        yield piece
        except APIError as exc:
            raise self._map_api_error(exc) from exc
        assembled = "".join(text_parts)
        try:
            if last_chunk is not None:
                parsed = self._parse_response(last_chunk, model=request.model)
                if not parsed.content and assembled:
                    parsed = parsed.model_copy(update={"content": assembled})
                self.last_stream_result = parsed
            else:
                raise ValueError("empty stream")
        except Exception:
            self.last_stream_result = ProviderResponse(
                content=assembled,
                usage=TokenUsage(),
                provider_name=self.provider_name,
                model=request.model,
            )

    async def health_check(self) -> bool:
        """Verify connectivity by listing available models."""
        try:
            async with genai.Client(api_key=self._api_key).aio as aclient:
                async for _ in aclient.models.list():
                    break
            healthy = True
        except Exception:
            healthy = False
        self._available = healthy
        logger.info("Gemini health check: %s", "OK" if healthy else "FAIL")
        return healthy

    # -- Request translation --------------------------------------------------

    def _build_contents(self, messages: list[ChatMessage]) -> tuple[list[dict[str, Any]], str | None]:
        """Convert OpenAI-style messages into Gemini contents and system instruction."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        # Gemini's function_response requires the function's name, but the
        # OpenAI tool-result message only carries tool_call_id: the name lives
        # on the assistant message that requested the call. Build the mapping
        # first so tool results can be resolved back to a name.
        tool_names = self._map_tool_call_ids(messages)

        for msg in messages:
            if msg.role == "system":
                text = msg.content if isinstance(msg.content, str) else str(msg.content)
                system_parts.append(text)
            else:
                contents.append(self._message_to_content(msg, tool_names))

        system_instruction = "\n".join(system_parts) if system_parts else None
        return contents, system_instruction

    @staticmethod
    def _map_tool_call_ids(messages: list[ChatMessage]) -> dict[str, str]:
        """Map tool_call_id -> function name from assistant tool calls."""
        mapping: dict[str, str] = {}
        for msg in messages:
            for tc in msg.tool_calls or []:
                call_id = tc.get("id")
                name = (tc.get("function") or {}).get("name")
                if call_id and name:
                    mapping[call_id] = name
        return mapping

    @staticmethod
    def _name_from_tool_call_id(tool_call_id: str | None) -> str:
        """Last-resort recovery of a function name from an id we issued.

        Ids are minted as ``call_<function name>_<random>``, so the name can be
        recovered when the conversation history is incomplete.
        """
        if not tool_call_id or not tool_call_id.startswith(_TOOL_CALL_ID_PREFIX):
            return ""
        remainder = tool_call_id[len(_TOOL_CALL_ID_PREFIX):]
        head, _, tail = remainder.rpartition("_")
        # Strip the random suffix only if it looks like one.
        if head and len(tail) == 8 and all(c in "0123456789abcdef" for c in tail):
            return head
        return remainder

    def _build_config(self, request: ChatCompletionRequest, system_instruction: str | None) -> dict[str, Any]:
        """Build the config dict for the SDK."""
        config: dict[str, Any] = {}
        
        if system_instruction:
            config["system_instruction"] = system_instruction
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_tokens is not None:
            config["max_output_tokens"] = request.max_tokens
        if request.top_p is not None:
            config["top_p"] = request.top_p
        if request.stop is not None:
            stop = request.stop if isinstance(request.stop, list) else [request.stop]
            config["stop_sequences"] = stop

        from gateway.providers.json_mode import gemini_json_config, json_mode_instruction
        config.update(gemini_json_config(request.response_format))
        extra = json_mode_instruction(request.response_format)
        if extra:
            config["system_instruction"] = (
                f"{system_instruction}\n\n{extra}" if system_instruction else extra
            )

        if request.tools:
            config["tools"] = self._translate_tools(request.tools)
            
        return config

    @classmethod
    def _message_to_content(
        cls,
        msg: ChatMessage,
        tool_names: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Map a single ChatMessage to a Gemini dict."""
        role = _ROLE_MAP.get(msg.role, "user")
        parts: list[dict[str, Any]] = []
        tool_names = tool_names or {}

        if msg.tool_call_id and msg.content is not None:
            # Resolve the function name: explicit `name` if the client sent one
            # (legacy OpenAI style), else the assistant message that requested
            # the call, else recover it from the id we issued. Gemini rejects an
            # empty name outright.
            name = (
                msg.name
                or tool_names.get(msg.tool_call_id)
                or cls._name_from_tool_call_id(msg.tool_call_id)
            )
            if not name:
                logger.warning(
                    "Could not resolve a function name for tool_call_id %s; "
                    "Gemini will reject this request.", msg.tool_call_id,
                )
            parts.append({
                "function_response": {
                    "name": name,
                    "response": {"result": msg.content},
                },
            })
        elif msg.tool_calls:
            for tc in msg.tool_calls:
                fn = tc.get("function", {})
                args = fn.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                part: dict[str, Any] = {
                    "function_call": {
                        "name": fn.get("name", ""),
                        "args": args,
                    },
                }
                # Gemini 3.x requires the original thought signature when a
                # function call is replayed as history.
                signature = _recall_thought_signature(tc.get("id"))
                if signature is not None:
                    part["thought_signature"] = signature
                parts.append(part)
        else:
            text = msg.content if isinstance(msg.content, str) else str(msg.content or "")
            parts.append({"text": text})

        return {"role": role, "parts": parts}

    @staticmethod
    def _translate_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI tools to Gemini format."""
        declarations: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            fn = tool.get("function", {})
            declarations.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            })
        if not declarations:
            return []
        return [{"function_declarations": declarations}]

    # -- Response parsing -----------------------------------------------------

    def _parse_response(self, response: Any, *, model: str) -> ProviderResponse:
        """Parse a genai SDK response into ``ProviderResponse``."""
        
        if not response.candidates:
            raise self._gateway_error(
                code="provider_empty_response",
                message="Gemini returned no candidates",
            )
            
        candidate = response.candidates[0]
        
        finish_reason = getattr(candidate, "finish_reason", "STOP")
        if hasattr(finish_reason, "name"):
            finish_reason = finish_reason.name.lower()
        elif isinstance(finish_reason, str):
            finish_reason = finish_reason.lower()

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if part.text:
                    text_parts.append(part.text)
                elif part.function_call:
                    fc = part.function_call
                    # Ids must be unique: the same function may be called more
                    # than once in a conversation, and each call has its own
                    # thought signature. A name-only id would collide.
                    call_id = f"{_TOOL_CALL_ID_PREFIX}{fc.name}_{uuid.uuid4().hex[:8]}"
                    _remember_thought_signature(
                        call_id, getattr(part, "thought_signature", None)
                    )
                    tool_calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": fc.name,
                            "arguments": json.dumps(fc.args or {}),
                        },
                    })

        content = "\n".join(text_parts) if text_parts else ""

        # Usage metadata
        # These counts are absent (None), not zero, in some responses — notably
        # when a thinking model spends its whole budget on reasoning tokens and
        # emits no completion. `getattr` alone still yields None there, which
        # then fails TokenUsage validation, so coerce explicitly.
        usage_meta = response.usage_metadata
        if usage_meta:
            prompt_tokens = getattr(usage_meta, "prompt_token_count", 0) or 0
            completion_tokens = getattr(usage_meta, "candidates_token_count", 0) or 0
            total_tokens = getattr(usage_meta, "total_token_count", 0) or 0
            cached_tokens = getattr(usage_meta, "cached_content_token_count", 0) or 0
            reasoning_tokens = getattr(usage_meta, "thoughts_token_count", 0) or 0
        else:
            prompt_tokens = completion_tokens = total_tokens = cached_tokens = 0
            reasoning_tokens = 0

        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
            cached_tokens=cached_tokens or None,
            # Reasoning tokens are billed, so surfacing them keeps cost
            # attribution honest for thinking models.
            reasoning_tokens=reasoning_tokens or None,
        )

        raw_response = {}
        try:
            if hasattr(response, "model_dump"):
                raw_response = response.model_dump(mode="json")
            elif hasattr(response, "to_dict"):
                raw_response = response.to_dict()
        except Exception:
            pass

        return ProviderResponse(
            content=content,
            role="assistant",
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
            usage=usage,
            raw_response=raw_response,
            provider_name=self.provider_name,
            model=model,
        )

    # -- Error helpers --------------------------------------------------------

    @staticmethod
    def _gateway_error(*, code: str, message: str) -> Exception:
        """Build a ``ValueError`` wrapping a ``GatewayError`` payload."""
        err = GatewayError(error="provider_error", code=code, message=message)
        return ValueError(err.model_dump_json())

    def _map_api_error(self, exc: APIError) -> Exception:
        """Map SDK APIError to gateway error codes."""
        status = exc.code
        match status:
            case 400:
                code, msg = "bad_request", "Gemini rejected the request as invalid"
            case 401 | 403:
                code, msg = "auth_error", "Gemini API key is invalid or lacks permission"
            case 404:
                code, msg = "model_not_found", "Requested model not found on Gemini"
            case 429:
                code, msg = "rate_limited", "Gemini rate limit exceeded"
            case _ if status and status >= 500:
                code, msg = "provider_server_error", f"Gemini returned {status}"
            case _:
                code, msg = "provider_error", f"Gemini HTTP {status}"

        err = GatewayError(
            error="provider_error",
            code=code,
            message=msg,
            details={"status": status, "body": str(exc)},
        )
        return ValueError(err.model_dump_json())
