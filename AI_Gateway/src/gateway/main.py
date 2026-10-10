"""
AI Gateway — FastAPI Application Entry Point

Standalone service that proxies LLM requests through a governance pipeline:
  Auth → Validate → Cache → Rate Limit → Budget → Route → Provider → Meter → Log
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gateway.config import get_settings
from gateway.dependencies import initialize_container, get_container


def _setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the gateway."""
    log_format = (
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: initialize on startup, cleanup on shutdown."""
    settings = get_settings()
    _setup_logging(settings.log_level)

    logger = logging.getLogger("gateway")
    logger.info("=" * 60)
    logger.info("AI Gateway starting up")
    logger.info("  Mode: %s", "DEVELOPMENT" if settings.is_dev_mode else "PRODUCTION")
    logger.info("  Host: %s:%d", settings.host, settings.port)
    logger.info("  Redis: %s", "connected" if settings.has_redis else "in-memory (dev)")
    logger.info("  OTEL: %s", "connected" if settings.has_otel else "console (dev)")
    logger.info("  Gemini: %s", "configured" if settings.gemini_api_key else "NOT configured")
    if settings.admin_key and len(settings.admin_key) < 16 and not settings.debug:
        logger.warning("GATEWAY_ADMIN_KEY is shorter than 16 characters; set a stronger key")
    elif settings.admin_key and len(settings.admin_key) < 16:
        logger.warning("GATEWAY_ADMIN_KEY is short (%s chars). Use 16+ characters in production.", len(settings.admin_key))
    logger.info("=" * 60)

    # Initialize all services
    container = await initialize_container(settings)

    yield

    # Shutdown
    await container.shutdown()
    logger.info("AI Gateway shut down cleanly")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="AI Gateway",
        description=(
            "Centralized LLM control plane for Logistics/SCM. "
            "Handles authentication, routing, rate limiting, cost tracking, "
            "and governance for all AI requests."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # CORS (permissive in dev, locked down in prod)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routes
    from gateway.api import chat_router, health_router, usage_router, admin_router, embeddings_router

    app.include_router(health_router, tags=["Health"])
    app.include_router(chat_router, tags=["Chat Completions"])
    app.include_router(embeddings_router, tags=["Embeddings"])
    app.include_router(usage_router, tags=["Usage & Models"])
    app.include_router(admin_router, tags=["Admin"])
    
    # Mount Static Files for Admin UI
    from fastapi.staticfiles import StaticFiles
    import os
    
    admin_ui_path = os.path.join(os.path.dirname(__file__), "admin_ui")
    os.makedirs(admin_ui_path, exist_ok=True)
    app.mount("/admin", StaticFiles(directory=admin_ui_path, html=True), name="admin_ui")

    # Mount Prometheus metrics endpoint
    from prometheus_client import make_asgi_app
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    return app


# Module-level app instance for `uvicorn gateway.main:app`
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "gateway.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
