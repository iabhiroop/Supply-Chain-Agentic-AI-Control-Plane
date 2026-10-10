"""
Budget models.

Budgets enforce spending limits at any level of the key hierarchy.
Hard limits block requests; soft limits emit alerts but allow through.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class BudgetPeriod(str, Enum):
    """Time period for budget resets."""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM = "custom"


class BudgetScope(str, Enum):
    """What level the budget applies to."""
    ORGANIZATION = "organization"
    TEAM = "team"
    PROJECT = "project"
    KEY = "key"


class Budget(BaseModel):
    """A spending limit applied to a scope (org, team, project, or key)."""
    id: UUID = Field(default_factory=uuid4)
    scope_type: BudgetScope = BudgetScope.PROJECT
    scope_id: str = Field(..., description="ID of the org/team/project/key this budget covers")
    
    # Limits
    limit_amount_usd: float = Field(..., description="Maximum spend in USD for the period")
    soft_threshold_pct: float = Field(80.0, description="Alert at this % of limit (e.g. 80)")
    hard_limit: bool = Field(True, description="If true, block requests when budget exceeded")

    # Period
    period: BudgetPeriod = BudgetPeriod.DAILY
    period_start: datetime = Field(default_factory=datetime.utcnow)
    
    # State (updated in real-time)
    spent_amount_usd: float = Field(0.0, description="Amount spent in current period")
    
    # Metadata
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_amount_usd - self.spent_amount_usd)

    @property
    def usage_pct(self) -> float:
        if self.limit_amount_usd <= 0:
            return 0.0
        return round((self.spent_amount_usd / self.limit_amount_usd) * 100, 2)

    @property
    def is_exceeded(self) -> bool:
        return self.spent_amount_usd >= self.limit_amount_usd

    @property
    def is_soft_threshold_reached(self) -> bool:
        return self.usage_pct >= self.soft_threshold_pct

    def can_afford(self, estimated_cost_usd: float) -> bool:
        """Check if the budget can afford an estimated cost."""
        if not self.hard_limit:
            return True  # Soft limit: always allow, just alert
        return (self.spent_amount_usd + estimated_cost_usd) <= self.limit_amount_usd

    def record_spend(self, cost_usd: float) -> None:
        """Record a spend against this budget."""
        self.spent_amount_usd += cost_usd
        self.updated_at = datetime.utcnow()


class BudgetStatus(BaseModel):
    """Budget status returned to API callers."""
    scope_type: str
    scope_id: str
    period: str
    limit_usd: float
    spent_usd: float
    remaining_usd: float
    usage_pct: float
    hard_limit: bool
    is_exceeded: bool
    period_start: datetime
