"""API routes package."""

from gateway.api.chat import router as chat_router
from gateway.api.health import router as health_router
from gateway.api.usage import router as usage_router
from gateway.api.admin import router as admin_router
from gateway.api.embeddings import router as embeddings_router

__all__ = ["chat_router", "health_router", "usage_router", "admin_router", "embeddings_router"]
