"""
Request Schema & Permission Validation.
"""

import logging
from typing import List, Tuple, Optional

from gateway.models.request import ChatCompletionRequest
from gateway.models.key import VirtualKey
from gateway.config import ModelsConfig

logger = logging.getLogger(__name__)

class RequestValidator:
    """Validates incoming requests against schemas and key permissions."""
    
    def __init__(self, models_config: ModelsConfig):
        self.models_config = models_config

    def validate_chat_request(self, request: ChatCompletionRequest, key: Optional[VirtualKey]) -> List[str]:
        """
        Validate a chat request.
        Returns a list of error strings. Empty list means valid.
        """
        errors = []
        
        # Check messages
        if not request.messages:
            errors.append("messages array cannot be empty")
            
        # Check key permissions
        if key:
            if not key.can_access_model(request.model):
                errors.append(f"Model '{request.model}' is not allowed for this API key")
                
            if key.permissions.max_tokens_per_request and request.max_tokens:
                if request.max_tokens > key.permissions.max_tokens_per_request:
                    errors.append(f"max_tokens ({request.max_tokens}) exceeds key limit ({key.permissions.max_tokens_per_request})")
                    
            if request.tools and not key.permissions.allow_tool_calls:
                errors.append("Tool calls are not allowed for this API key")
                
        # We don't strictly validate if the model exists here, because routing rules might remap it.
        # But we could check if max_tokens is within absolute limits.
        
        return errors

    def validate_model_exists(self, model: str) -> Optional[Tuple[str, str]]:
        """
        Check if a model exists across any provider.
        Returns (provider_name, model_name) if found, else None.
        """
        for prov_name, prov_data in self.models_config.providers.items():
            if model in prov_data.get("models", {}):
                return (prov_name, model)
        return None
