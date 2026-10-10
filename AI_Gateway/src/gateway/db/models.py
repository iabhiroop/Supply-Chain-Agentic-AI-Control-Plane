"""
SQLAlchemy ORM Models.
"""

from datetime import datetime
import uuid
from typing import Optional, Any

from sqlalchemy import String, Float, Boolean, Integer, DateTime, JSON, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncAttrs

class Base(AsyncAttrs, DeclarativeBase):
    pass

class VirtualKeyModel(Base):
    """Database model for a Virtual Key."""
    __tablename__ = "virtual_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    key_prefix: Mapped[str] = mapped_column(String(50))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(255), default="")
    
    # Hierarchy
    organization_id: Mapped[str] = mapped_column(String(100), default="default", index=True)
    team_id: Mapped[str] = mapped_column(String(100), default="default", index=True)
    project_id: Mapped[str] = mapped_column(String(100), default="default", index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(100))
    
    # JSON blobs for nested pydantic models
    permissions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rate_limits_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

class BudgetModel(Base):
    """Database model for a Budget."""
    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scope_type: Mapped[str] = mapped_column(String(50))  # organization, team, project, key
    scope_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    
    limit_amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    spent_amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    soft_threshold_pct: Mapped[float] = mapped_column(Float, default=80.0)
    hard_limit: Mapped[bool] = mapped_column(Boolean, default=True)
    
    period: Mapped[str] = mapped_column(String(50), default="daily")
    period_start: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UsageRecordModel(Base):
    """Database model for a single request usage record."""
    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    gateway_request_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    
    key_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("virtual_keys.id"), index=True)
    organization_id: Mapped[str] = mapped_column(String(100), index=True)
    team_id: Mapped[str] = mapped_column(String(100), index=True)
    project_id: Mapped[str] = mapped_column(String(100), index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    
    provider: Mapped[str] = mapped_column(String(50), index=True)
    model: Mapped[str] = mapped_column(String(100), index=True)
    endpoint: Mapped[str] = mapped_column(String(100))
    
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    
    route_strategy: Mapped[str] = mapped_column(String(50))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    route_reason: Mapped[str] = mapped_column(String(100), default="")
    error_code: Mapped[str] = mapped_column(String(80), default="")
    tpm_remaining: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cache_cost_avoided_usd: Mapped[float] = mapped_column(Float, default=0.0)
    extra_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class RegisteredModelModel(Base):
    """Admin-registered model overlay (YAML is the seed)."""
    __tablename__ = "registered_models"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    model: Mapped[str] = mapped_column(String(150), index=True)
    display_name: Mapped[str] = mapped_column(String(150), default="")
    status: Mapped[str] = mapped_column(String(50), default="active")
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProviderKeyModel(Base):
    """Encrypted upstream provider credentials."""
    __tablename__ = "provider_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(100), default="default")
    key_ciphertext: Mapped[str] = mapped_column(String(4000), default="")
    key_last4: Mapped[str] = mapped_column(String(8), default="")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AlertWebhookModel(Base):
    __tablename__ = "alert_webhooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
