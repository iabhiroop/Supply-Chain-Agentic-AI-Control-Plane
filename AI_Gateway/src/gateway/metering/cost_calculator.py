"""Cost Calculation."""

from gateway.config import ModelsConfig
from gateway.models.request import TokenUsage

class CostCalculator:
    """Calculates cost based on model pricing."""
    
    def __init__(self, models_config: ModelsConfig):
        self.models_config = models_config

    def calculate_cost(self, provider: str, model: str, usage: TokenUsage) -> float:
        """Calculate exact cost post-request."""
        pricing_dict = self.models_config.get_pricing(provider, model)
        
        cost = 0.0
        cost += (usage.prompt_tokens / 1_000_000) * pricing_dict.get("input_per_1m_tokens", 0.0)
        cost += (usage.completion_tokens / 1_000_000) * pricing_dict.get("output_per_1m_tokens", 0.0)
        if usage.cached_tokens:
            cost += (usage.cached_tokens / 1_000_000) * pricing_dict.get("cached_input_per_1m_tokens", 0.0)
        if usage.reasoning_tokens:
            cost += (usage.reasoning_tokens / 1_000_000) * pricing_dict.get("thinking_per_1m_tokens", 0.0)
            
        return round(cost, 8)

    def estimate_cost(self, provider: str, model: str, estimated_input_tokens: int, estimated_output_tokens: int = 0) -> float:
        """Estimate cost pre-request for budget gate."""
        pricing_dict = self.models_config.get_pricing(provider, model)
        
        cost = 0.0
        cost += (estimated_input_tokens / 1_000_000) * pricing_dict.get("input_per_1m_tokens", 0.0)
        cost += (estimated_output_tokens / 1_000_000) * pricing_dict.get("output_per_1m_tokens", 0.0)
        
        return round(cost, 8)
