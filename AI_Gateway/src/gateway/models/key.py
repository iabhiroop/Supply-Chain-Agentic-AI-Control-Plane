"""
Virtual API Key models.

Keys follow a hierarchy: Organization → Team → Project → Agent.
Each key is scoped with permissions, rate limits, allowed models, and a budget.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class KeyPermissions(BaseModel):
    """Fine-grained permissions attached to a virtual key."""
    allowed_models: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Model names this key can access. '*' means all."
    )
    allowed_endpoints: list[str] = Field(
        default_factory=lambda: ["/v1/chat/completions", "/v1/embeddings"],
        description="API endpoints this key can call."
    )
    allowed_tools: list[str] = Field(
        default_factory=lambda: ["*"],
        description="List of allowed tool names. '*' means all."
    )
    max_tokens_per_request: int | None = Field(
        None, description="Cap on max_tokens per single request."
    )
    allow_tool_calls: bool = True
    tags: list[str] = Field(
        default_factory=list,
        description="Tags for routing rules (e.g. 'compliance', 'extraction')."
    )


class RateLimits(BaseModel):
    """Rate limit configuration for a key."""
    rpm: int = Field(60, description="Requests per minute")
    tpm: int = Field(100_000, description="Tokens per minute")
    rpd: int | None = Field(None, description="Requests per day (optional)")


class VirtualKey(BaseModel):
    """A virtual API key that authenticates and authorizes gateway requests."""
    id: UUID = Field(default_factory=uuid4)
    key_prefix: str = Field("", description="Human-readable prefix: vk_teamname_...")
    key_hash: str = Field("", description="SHA-256 hash of the full key (stored, never the raw key)")
    name: str = Field("", description="Friendly name for this key")
    description: str = ""

    # Hierarchy
    organization_id: str = "default"
    team_id: str = "default"
    project_id: str = "default"
    agent_id: str | None = None

    # Access control
    permissions: KeyPermissions = Field(default_factory=KeyPermissions)
    rate_limits: RateLimits = Field(default_factory=RateLimits)

    # State
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    last_used_at: datetime | None = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def is_valid(self) -> bool:
        return self.is_active and not self.is_expired()

    def can_access_model(self, model_name: str) -> bool:
        allowed = self.permissions.allowed_models
        if "*" in allowed:
            return True
        return model_name in allowed

    def can_access_endpoint(self, endpoint: str) -> bool:
        allowed = self.permissions.allowed_endpoints
        if "*" in allowed:
            return True
        return endpoint in allowed

    @staticmethod
    def generate_key(prefix: str = "vk") -> tuple[str, str]:
        """
        Generate a new virtual key.
        Returns (raw_key, key_hash). Only the hash is stored.
        """
        random_part = secrets.token_urlsafe(32)
        raw_key = f"{prefix}_{random_part}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        return raw_key, key_hash

    @staticmethod
    def hash_key(raw_key: str) -> str:
        """Hash a raw key for lookup."""
        return hashlib.sha256(raw_key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# API schemas for key management
# ---------------------------------------------------------------------------

class KeyCreate(BaseModel):
    """Request body for creating a new virtual key."""
    name: str
    description: str = ""
    organization_id: str = "default"
    team_id: str = "default"
    project_id: str = "default"
    agent_id: str | None = None
    permissions: KeyPermissions = Field(default_factory=KeyPermissions)
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    expires_at: datetime | None = None


class KeyResponse(BaseModel):
    """Response when a key is created — includes the raw key ONCE."""
    id: UUID
    key: str = Field("", description="Raw key value. Only shown on creation.")
    key_prefix: str
    name: str
    organization_id: str
    team_id: str
    project_id: str
    is_active: bool
    created_at: datetime
    expires_at: datetime | None = None
