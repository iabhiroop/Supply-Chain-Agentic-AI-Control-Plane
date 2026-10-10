"""
Provider & model configuration models.

These represent the parsed config/models.yaml structure as typed objects
used by the router, cost calculator, and model catalog.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModelPricing(BaseModel):
    """Pricing per 1 million tokens for a specific model."""
    input_per_1m_tokens: float = 0.0
    output_per_1m_tokens: float = 0.0
    cached_input_per_1m_tokens: float = 0.0
    thinking_per_1m_tokens: float = 0.0

    def cost_for_tokens(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> float:
        """Calculate total cost in USD for a given token breakdown."""
        cost = 0.0
        cost += (input_tokens / 1_000_000) * self.input_per_1m_tokens
        cost += (output_tokens / 1_000_000) * self.output_per_1m_tokens
        cost += (cached_tokens / 1_000_000) * self.cached_input_per_1m_tokens
        cost += (reasoning_tokens / 1_000_000) * self.thinking_per_1m_tokens
        return round(cost, 8)


class ModelConfig(BaseModel):
    """Configuration for a single model within a provider."""
    name: str = ""
    provider: str = ""
    context_window: int = 128_000
    max_output_tokens: int = 8192
    capabilities: list[str] = Field(default_factory=list)
    pricing: ModelPricing = Field(default_factory=ModelPricing)
    status: str = "active"

    @property
    def supports_function_calling(self) -> bool:
        return "function_calling" in self.capabilities

    @property
    def supports_vision(self) -> bool:
        return "vision" in self.capabilities

    @property
    def supports_thinking(self) -> bool:
        return "thinking" in self.capabilities


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider (Gemini, Ollama, etc.)."""
    name: str = ""
    display_name: str = ""
    type: str = "cloud"  # "cloud" or "local"
    auth_type: str = "api_key"  # "api_key", "none", "bearer"
    default_timeout_seconds: int = 60
    models: dict[str, ModelConfig] = Field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return self.type == "local"

    def get_model(self, model_name: str) -> ModelConfig | None:
        return self.models.get(model_name)

    def list_active_models(self) -> list[str]:
        return [
            name for name, m in self.models.items()
            if m.status == "active"
        ]
