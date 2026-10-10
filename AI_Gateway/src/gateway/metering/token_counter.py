"""Token counting and estimation."""

from typing import Dict, Any

from gateway.models.request import TokenUsage, ChatCompletionRequest

class TokenCounter:
    """Handles token counting and estimation across providers."""
    
    def count_from_provider_response(self, provider_name: str, raw_response: Dict[str, Any]) -> TokenUsage:
        """Extract usage from raw provider response if the provider adapter hasn't already."""
        # Typically the Provider Adapters do this. This is a fallback/utility.
        return TokenUsage()
        
    def estimate_tokens(self, text: str) -> int:
        """Rough estimation (approx 4 chars per token)."""
        if not text: return 0
        return max(1, len(text) // 4)
        
    def estimate_request_tokens(self, request: ChatCompletionRequest) -> int:
        """Estimate tokens for an entire request."""
        total = 0
        for msg in request.messages:
            if isinstance(msg.content, str):
                total += self.estimate_tokens(msg.content)
            elif isinstance(msg.content, list):
                for part in msg.content:
                    if isinstance(part, dict) and "text" in part:
                        total += self.estimate_tokens(part["text"])
        return total
