"""
Provider Base — Abstract interface for LLM provider adapters.

Every concrete provider (Gemini, Ollama, …) inherits from BaseProvider and
implements the translation layer between the gateway's OpenAI-compatible
request format and the provider's native API.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator

from pydantic import BaseModel, Field

from gateway.models.request import ChatCompletionRequest, TokenUsage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider Response — unified output regardless of backend
# ---------------------------------------------------------------------------

class ProviderResponse(BaseModel):
    """Normalised response returned by every provider adapter."""

    content: str
    role: str = "assistant"
    tool_calls: list[dict] | None = None
    finish_reason: str = "stop"
    usage: TokenUsage
    raw_response: dict = Field(default_factory=dict)
    provider_name: str = ""
    model: str = ""


# ---------------------------------------------------------------------------
# Abstract Base Provider
# ---------------------------------------------------------------------------

class BaseProvider(ABC):
    """Abstract base class that every LLM provider adapter must implement.

    Subclasses translate between the gateway's *OpenAI-compatible*
    ``ChatCompletionRequest`` and the provider's native API format, and
    normalise responses into a ``ProviderResponse``.
    """

    def __init__(self, config: dict) -> None:
        """Store the provider configuration dictionary.

        Parameters
        ----------
        config:
            Arbitrary provider-specific settings (API keys, base URLs,
            timeout overrides, etc.).
        """
        self._config = config
        self._available = True
        self.last_stream_result: ProviderResponse | None = None

    # -- Abstract interface ---------------------------------------------------

    @abstractmethod
    async def complete(self, request: ChatCompletionRequest) -> ProviderResponse:
        """Send a chat-completion request and return a normalised response.

        Raises
        ------
        httpx.HTTPStatusError / GatewayError
            On transport or provider-side errors.
        """

    async def stream_text(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Yield assistant text deltas. Subclasses override for true streaming."""
        result = await self.complete(request)
        self.last_stream_result = result
        if result.content:
            yield result.content

    last_stream_result: ProviderResponse | None = None

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (e.g. ``'gemini'``, ``'ollama'``)."""

    # -- Concrete helpers -----------------------------------------------------

    @property
    def is_available(self) -> bool:
        """Whether the provider is currently considered available.

        Updated by health checks or circuit-breaker logic.
        """
        return self._available

    def set_credentials(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        if api_key is not None:
            self._config["api_key"] = api_key
        if base_url is not None:
            self._config["base_url"] = base_url

    async def close(self) -> None:
        return None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} provider={self.provider_name} available={self.is_available}>"
