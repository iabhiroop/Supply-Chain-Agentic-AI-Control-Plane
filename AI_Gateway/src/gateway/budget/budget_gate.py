"""
Budget Tracking & Enforcement.
"""

import logging
from typing import Dict, Optional
from pydantic import BaseModel

from gateway.models.budget import Budget, BudgetStatus, BudgetScope, BudgetPeriod

logger = logging.getLogger(__name__)

class BudgetCheckResult(BaseModel):
    allowed: bool
    remaining_usd: float
    usage_pct: float
    hard_limit: bool
    reason: Optional[str] = None

class BudgetManager:
    """In-memory budget tracking for dev mode."""
    
    def __init__(self, db: Optional['DatabaseRepository'] = None):
        self.db = db
        # Maps scope_id (e.g., project_id) -> Budget
        self._budgets: Dict[str, Budget] = {}
        if self.db:
            logger.info("Initialized DB-backed BudgetManager")
        else:
            logger.info("Initialized in-memory BudgetManager")

    async def create_budget(self, budget: Budget) -> Budget:
        """Create or update a budget."""
        if self.db:
            return await self.db.create_budget(budget)
            
        self._budgets[budget.scope_id] = budget
        logger.info(f"Created budget for {budget.scope_type} {budget.scope_id}: ${budget.limit_amount_usd}")
        return budget

    async def ensure_budget(self, scope_id: str, limit_usd: float, spent_usd: float = 0.0) -> Budget:
        """Create a project budget if one does not exist so spend is actually recorded."""
        if self.db:
            existing = await self.db.get_budget(scope_id)
        else:
            existing = self._budgets.get(scope_id)
        if existing:
            return existing
        budget = Budget(
            scope_type=BudgetScope.PROJECT,
            scope_id=scope_id,
            limit_amount_usd=limit_usd,
            spent_amount_usd=spent_usd,
            hard_limit=True,
            period=BudgetPeriod.DAILY,
        )
        return await self.create_budget(budget)

    async def check_budget(self, scope_id: str, estimated_cost_usd: float) -> BudgetCheckResult:
        """Check if a request is affordable within the budget."""
        if self.db:
            budget = await self.db.get_budget(scope_id)
        else:
            budget = self._budgets.get(scope_id)
        
        # If no budget exists, assume infinite budget in dev mode
        if not budget:
            return BudgetCheckResult(
                allowed=True,
                remaining_usd=float('inf'),
                usage_pct=0.0,
                hard_limit=False
            )
            
        can_afford = budget.can_afford(estimated_cost_usd)
        
        return BudgetCheckResult(
            allowed=can_afford,
            remaining_usd=budget.remaining_usd,
            usage_pct=budget.usage_pct,
            hard_limit=budget.hard_limit,
            reason="Budget exceeded" if not can_afford else None
        )

    async def record_spend(self, scope_id: str, cost_usd: float, *, default_limit_usd: float | None = None) -> None:
        """Record actual spend against a budget."""
        if cost_usd <= 0:
            return
        if self.db:
            existing = await self.db.get_budget(scope_id)
            if not existing:
                await self.ensure_budget(scope_id, default_limit_usd or 50.0)
            await self.db.record_spend(scope_id, cost_usd)
            return

        budget = self._budgets.get(scope_id)
        if not budget and default_limit_usd is not None:
            budget = await self.ensure_budget(scope_id, default_limit_usd)
        if budget:
            budget.record_spend(cost_usd)

    async def get_status(self, scope_id: str) -> Optional[BudgetStatus]:
        """Get the current status of a budget."""
        if self.db:
            budget = await self.db.get_budget(scope_id)
        else:
            budget = self._budgets.get(scope_id)
            
        if not budget:
            return None
            
        return BudgetStatus(
            scope_type=budget.scope_type.value,
            scope_id=budget.scope_id,
            period=budget.period.value,
            limit_usd=budget.limit_amount_usd,
            spent_usd=budget.spent_amount_usd,
            remaining_usd=budget.remaining_usd,
            usage_pct=budget.usage_pct,
            hard_limit=budget.hard_limit,
            is_exceeded=budget.is_exceeded,
            period_start=budget.period_start
        )
