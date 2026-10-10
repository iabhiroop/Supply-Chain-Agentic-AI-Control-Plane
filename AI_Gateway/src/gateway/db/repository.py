"""
Database Repository mapping Pydantic domain models to SQLAlchemy models.
"""

import logging
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.models.key import VirtualKey, KeyPermissions, RateLimits, KeyCreate
from gateway.models.budget import Budget, BudgetPeriod, BudgetScope
from gateway.models.usage import UsageRecord
from gateway.db.models import (
    Base,
    VirtualKeyModel,
    BudgetModel,
    UsageRecordModel,
    RegisteredModelModel,
    ProviderKeyModel,
    AlertWebhookModel,
)

logger = logging.getLogger(__name__)

class DatabaseRepository:
    """Handles all database operations using SQLAlchemy."""
    
    def __init__(self, database_url: str):
        self.engine = create_async_engine(database_url, echo=False)
        self.session_maker = async_sessionmaker(self.engine, expire_on_commit=False)
        logger.info(f"Initialized DatabaseRepository with {database_url}")

    async def initialize_schema(self) -> None:
        """Create tables if they don't exist. Useful for SQLite."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_ensure_usage_columns)
            
    async def close(self) -> None:
        await self.engine.dispose()

    # --- Keys ---

    async def create_key(self, raw_key: str, key_hash: str, request: KeyCreate) -> VirtualKey:
        vk = VirtualKey(
            key_prefix=raw_key.split('_')[0] + "_" if "_" in raw_key else "vk_",
            key_hash=key_hash,
            name=request.name,
            description=request.description,
            organization_id=request.organization_id,
            team_id=request.team_id,
            project_id=request.project_id,
            agent_id=request.agent_id,
            permissions=request.permissions,
            rate_limits=request.rate_limits,
            expires_at=request.expires_at
        )
        
        db_key = VirtualKeyModel(
            id=vk.id,
            key_prefix=vk.key_prefix,
            key_hash=vk.key_hash,
            name=vk.name,
            description=vk.description,
            organization_id=vk.organization_id,
            team_id=vk.team_id,
            project_id=vk.project_id,
            agent_id=vk.agent_id,
            permissions_json=vk.permissions.model_dump(),
            rate_limits_json=vk.rate_limits.model_dump(),
            is_active=vk.is_active,
            created_at=vk.created_at,
            expires_at=vk.expires_at,
        )
        
        async with self.session_maker() as session:
            session.add(db_key)
            await session.commit()
            
        return vk

    async def get_key_by_hash(self, key_hash: str) -> Optional[VirtualKey]:
        async with self.session_maker() as session:
            stmt = select(VirtualKeyModel).where(VirtualKeyModel.key_hash == key_hash)
            result = await session.execute(stmt)
            db_key = result.scalar_one_or_none()
            
            if not db_key:
                return None
                
            return self._map_key(db_key)
            
    async def get_key_by_id(self, key_id: UUID) -> Optional[VirtualKey]:
        async with self.session_maker() as session:
            stmt = select(VirtualKeyModel).where(VirtualKeyModel.id == key_id)
            result = await session.execute(stmt)
            db_key = result.scalar_one_or_none()
            if not db_key:
                return None
            return self._map_key(db_key)

    async def list_keys(self) -> List[VirtualKey]:
        async with self.session_maker() as session:
            stmt = select(VirtualKeyModel)
            result = await session.execute(stmt)
            return [self._map_key(db_key) for db_key in result.scalars().all()]

    async def update_key_last_used(self, key_id: UUID) -> None:
        async with self.session_maker() as session:
            stmt = update(VirtualKeyModel).where(VirtualKeyModel.id == key_id).values(last_used_at=datetime.utcnow())
            await session.execute(stmt)
            await session.commit()
            
    async def revoke_key(self, key_id: UUID) -> bool:
        async with self.session_maker() as session:
            stmt = update(VirtualKeyModel).where(VirtualKeyModel.id == key_id).values(is_active=False)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    def _map_key(self, db_key: VirtualKeyModel) -> VirtualKey:
        return VirtualKey(
            id=db_key.id,
            key_prefix=db_key.key_prefix,
            key_hash=db_key.key_hash,
            name=db_key.name,
            description=db_key.description,
            organization_id=db_key.organization_id,
            team_id=db_key.team_id,
            project_id=db_key.project_id,
            agent_id=db_key.agent_id,
            permissions=KeyPermissions(**db_key.permissions_json),
            rate_limits=RateLimits(**db_key.rate_limits_json),
            is_active=db_key.is_active,
            created_at=db_key.created_at,
            expires_at=db_key.expires_at,
            last_used_at=db_key.last_used_at
        )

    # --- Budgets ---

    async def create_budget(self, budget: Budget) -> Budget:
        db_budget = BudgetModel(
            id=budget.id,
            scope_type=budget.scope_type.value,
            scope_id=budget.scope_id,
            limit_amount_usd=budget.limit_amount_usd,
            spent_amount_usd=budget.spent_amount_usd,
            soft_threshold_pct=budget.soft_threshold_pct,
            hard_limit=budget.hard_limit,
            period=budget.period.value,
            period_start=budget.period_start,
            is_active=budget.is_active,
            created_at=budget.created_at,
            updated_at=budget.updated_at,
        )
        async with self.session_maker() as session:
            session.add(db_budget)
            await session.commit()
        return budget

    async def get_budget(self, scope_id: str) -> Optional[Budget]:
        async with self.session_maker() as session:
            stmt = select(BudgetModel).where(BudgetModel.scope_id == scope_id)
            result = await session.execute(stmt)
            db_budget = result.scalar_one_or_none()
            if not db_budget:
                return None
            
            return Budget(
                id=db_budget.id,
                scope_type=BudgetScope(db_budget.scope_type),
                scope_id=db_budget.scope_id,
                limit_amount_usd=db_budget.limit_amount_usd,
                soft_threshold_pct=db_budget.soft_threshold_pct,
                hard_limit=db_budget.hard_limit,
                period=BudgetPeriod(db_budget.period),
                period_start=db_budget.period_start,
                spent_amount_usd=db_budget.spent_amount_usd,
                is_active=db_budget.is_active,
                created_at=db_budget.created_at,
                updated_at=db_budget.updated_at,
            )

    async def record_spend(self, scope_id: str, cost_usd: float) -> None:
        async with self.session_maker() as session:
            # Simple atomic-ish update, though for high concurrency you'd want proper locking
            stmt = update(BudgetModel).where(BudgetModel.scope_id == scope_id).values(
                spent_amount_usd=BudgetModel.spent_amount_usd + cost_usd,
                updated_at=datetime.utcnow()
            )
            await session.execute(stmt)
            await session.commit()

    # --- Usage Records ---

    async def create_usage_record(self, record: UsageRecord) -> None:
        db_record = UsageRecordModel(
            id=record.id,
            gateway_request_id=record.gateway_request_id,
            key_id=record.key_id,
            organization_id=record.organization_id,
            team_id=record.team_id,
            project_id=record.project_id,
            agent_id=record.agent_id,
            provider=record.provider,
            model=record.model,
            endpoint=record.endpoint,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            total_tokens=record.total_tokens,
            cached_tokens=record.cached_tokens,
            reasoning_tokens=record.reasoning_tokens,
            cost_usd=record.cost_usd,
            latency_ms=record.latency_ms,
            route_strategy=record.route_strategy,
            cache_hit=record.cache_hit,
            fallback_used=record.fallback_used,
            route_reason=record.route_reason,
            error_code=record.error_code,
            tpm_remaining=record.tpm_remaining,
            cache_cost_avoided_usd=record.cache_cost_avoided_usd,
            extra_json=record.metadata or {},
            created_at=record.created_at
        )
        async with self.session_maker() as session:
            session.add(db_record)
            await session.commit()

    async def get_usage_records(self, since: datetime) -> List[UsageRecord]:
        async with self.engine.begin() as conn:
            await conn.run_sync(_ensure_usage_columns)
        async with self.session_maker() as session:
            stmt = select(UsageRecordModel).where(UsageRecordModel.created_at >= since)
            result = await session.execute(stmt)
            db_records = result.scalars().all()
            
            records = []
            for db_r in db_records:
                meta = getattr(db_r, "extra_json", None) or {}
                records.append(UsageRecord(
                    id=db_r.id,
                    gateway_request_id=db_r.gateway_request_id,
                    key_id=db_r.key_id,
                    organization_id=db_r.organization_id,
                    team_id=db_r.team_id,
                    project_id=db_r.project_id,
                    agent_id=db_r.agent_id,
                    provider=db_r.provider,
                    model=db_r.model,
                    endpoint=db_r.endpoint,
                    input_tokens=db_r.input_tokens,
                    output_tokens=db_r.output_tokens,
                    total_tokens=db_r.total_tokens,
                    cached_tokens=db_r.cached_tokens,
                    reasoning_tokens=db_r.reasoning_tokens,
                    cost_usd=db_r.cost_usd,
                    latency_ms=db_r.latency_ms,
                    route_strategy=db_r.route_strategy,
                    cache_hit=db_r.cache_hit,
                    fallback_used=bool(getattr(db_r, "fallback_used", False) or meta.get("fallback_used")),
                    route_reason=getattr(db_r, "route_reason", "") or meta.get("route_reason") or "",
                    error_code=getattr(db_r, "error_code", "") or meta.get("error_code") or "",
                    tpm_remaining=getattr(db_r, "tpm_remaining", None)
                    if getattr(db_r, "tpm_remaining", None) is not None
                    else meta.get("tpm_remaining"),
                    cache_cost_avoided_usd=float(
                        getattr(db_r, "cache_cost_avoided_usd", 0)
                        or meta.get("cache_cost_avoided_usd")
                        or 0
                    ),
                    metadata=meta,
                    created_at=db_r.created_at
                ))
            return records

    # --- Registered models ---

    async def upsert_registered_model(self, provider: str, model: str, spec: dict, status: str = "active") -> dict:
        async with self.session_maker() as session:
            stmt = select(RegisteredModelModel).where(
                RegisteredModelModel.provider == provider,
                RegisteredModelModel.model == model,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                row = RegisteredModelModel(
                    provider=provider,
                    model=model,
                    display_name=spec.get("display_name") or model,
                    status=status,
                    spec_json=spec,
                )
                session.add(row)
            else:
                row.spec_json = spec
                row.status = status
                row.display_name = spec.get("display_name") or model
                row.updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(row)
            return self._map_registered(row)

    async def list_registered_models(self) -> list[dict]:
        async with self.session_maker() as session:
            result = await session.execute(select(RegisteredModelModel))
            return [self._map_registered(r) for r in result.scalars().all()]

    async def delete_registered_model(self, provider: str, model: str) -> bool:
        async with self.session_maker() as session:
            stmt = select(RegisteredModelModel).where(
                RegisteredModelModel.provider == provider,
                RegisteredModelModel.model == model,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if not row:
                return False
            await session.delete(row)
            await session.commit()
            return True

    @staticmethod
    def _map_registered(row: RegisteredModelModel) -> dict:
        return {
            "id": str(row.id),
            "provider": row.provider,
            "model": row.model,
            "display_name": row.display_name,
            "status": row.status,
            "spec": row.spec_json or {},
        }

    # --- Provider keys ---

    async def create_provider_key(self, provider: str, name: str, ciphertext: str, last4: str, base_url: str = "") -> dict:
        row = ProviderKeyModel(
            provider=provider,
            name=name,
            key_ciphertext=ciphertext,
            key_last4=last4,
            base_url=base_url,
        )
        async with self.session_maker() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return self._map_provider_key(row)

    async def list_provider_keys(self, provider: str | None = None) -> list[dict]:
        async with self.session_maker() as session:
            stmt = select(ProviderKeyModel)
            if provider:
                stmt = stmt.where(ProviderKeyModel.provider == provider)
            result = await session.execute(stmt)
            return [self._map_provider_key(r) for r in result.scalars().all()]

    async def get_provider_key(self, key_id: UUID) -> Optional[dict]:
        async with self.session_maker() as session:
            stmt = select(ProviderKeyModel).where(ProviderKeyModel.id == key_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._map_provider_key(row, include_secret=True) if row else None

    async def delete_provider_key(self, key_id: UUID) -> bool:
        async with self.session_maker() as session:
            stmt = select(ProviderKeyModel).where(ProviderKeyModel.id == key_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if not row:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def set_provider_key_active(self, key_id: UUID, is_active: bool) -> bool:
        async with self.session_maker() as session:
            stmt = update(ProviderKeyModel).where(ProviderKeyModel.id == key_id).values(is_active=is_active)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    @staticmethod
    def _map_provider_key(row: ProviderKeyModel, include_secret: bool = False) -> dict:
        data = {
            "id": str(row.id),
            "provider": row.provider,
            "name": row.name,
            "key_last4": row.key_last4,
            "base_url": row.base_url,
            "is_active": row.is_active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if include_secret:
            data["key_ciphertext"] = row.key_ciphertext
        return data

    async def list_alert_webhooks(self) -> list[str]:
        async with self.session_maker() as session:
            result = await session.execute(select(AlertWebhookModel).where(AlertWebhookModel.is_active.is_(True)))
            return [r.url for r in result.scalars().all()]

    async def add_alert_webhook(self, url: str) -> None:
        async with self.session_maker() as session:
            session.add(AlertWebhookModel(url=url))
            await session.commit()


def _ensure_usage_columns(sync_conn) -> None:
    """Best-effort ALTER for existing SQLite/Postgres usage_records tables."""
    statements = [
        "ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS fallback_used BOOLEAN DEFAULT FALSE",
        "ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS route_reason VARCHAR(100) DEFAULT ''",
        "ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS error_code VARCHAR(80) DEFAULT ''",
        "ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS tpm_remaining INTEGER",
        "ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS cache_cost_avoided_usd FLOAT DEFAULT 0",
        "ALTER TABLE usage_records ADD COLUMN IF NOT EXISTS extra_json JSON",
    ]
    for sql in statements:
        try:
            sync_conn.exec_driver_sql(sql)
        except Exception:
            pass
