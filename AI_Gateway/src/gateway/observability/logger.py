"""Structured Request Logger."""

import logging
from typing import Dict, Any, List, Optional

from gateway.models.usage import UsageRecord
from gateway.observability.pii import PIIRedactor

logger = logging.getLogger(__name__)

class RequestLogger:
    """Logs gateway requests and maintains an in-memory usage store for dev mode."""
    
    def __init__(self, db: Optional['DatabaseRepository'] = None):
        self.db = db
        self._records: List[UsageRecord] = []
        self.pii = PIIRedactor()
        if self.db:
            logger.info("Initialized DB-backed RequestLogger")
        else:
            logger.info("Initialized in-memory RequestLogger")

    def log_request(self, record: UsageRecord) -> None:
        """Log a completed request. (Sync wrapper, should be async in real prod but works for egress)."""
        # In a real async framework with middleware, the egress should await log_request.
        # Since we might not want to block, we could use asyncio.create_task or BackgroundTasks.
        # For simplicity, we just store it in memory for now, and we will update api/chat.py to await an async version.
        self._records.append(record)
        
        log_data = {
            "request_id": record.gateway_request_id,
            "provider": record.provider,
            "model": record.model,
            "cost_usd": record.cost_usd,
            "latency_ms": record.latency_ms,
            "cache_hit": record.cache_hit,
            "tokens": record.total_tokens
        }
        logger.info(f"Request completed: {log_data}")

    async def async_log_request(self, record: UsageRecord) -> None:
        """Async version to actually write to the database."""
        self.log_request(record) # still log to console
        if self.db:
            try:
                await self.db.create_usage_record(record)
            except Exception as e:
                logger.error(f"Failed to write usage record to DB: {e}")

    def log_error(self, gateway_request_id: str, error: str, details: Dict = None) -> None:
        safe_details = self.pii.redact_dict(details) if details else None
        logger.error(f"Gateway error [{gateway_request_id}]: {error} - {safe_details}")

    def log_rate_limit(self, key_id: str, reason: str) -> None:
        logger.warning(f"Rate limit triggered for key {key_id}: {reason}")

    def log_budget_alert(self, scope_id: str, usage_pct: float, limit_usd: float) -> None:
        logger.warning(f"Budget alert for {scope_id}: {usage_pct}% of ${limit_usd} used")
        
    async def get_records(self, since) -> List[UsageRecord]:
        if self.db:
            return await self.db.get_usage_records(since)
        return [r for r in self._records if r.created_at >= since]
