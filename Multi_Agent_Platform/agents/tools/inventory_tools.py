"""Inventory / WMS tools for the Smart Stock Adjustment agent."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.tools.client import api_get, api_post


class ProductInput(BaseModel):
    product_id: int = Field(description="The product's numeric identifier")


@tool("get_bin_stock", args_schema=ProductInput)
def get_bin_stock(product_id: int):
    """Get the stock the system believes is on hand for a product, per storage bin.

    Returns bin_id, quantity, warehouse and zone. Use this first when
    investigating a discrepancy: it shows what the system thinks, which you then
    compare against the physically counted quantity reported in the event.
    """
    return api_get(f"/inventory/bin/{product_id}")


@tool("get_stock_ledger", args_schema=ProductInput)
def get_stock_ledger(product_id: int):
    """Get the movement history (receipts, issues, adjustments, cycle counts) for a product.

    Use this to work out the root cause of a discrepancy: look for a missing
    issue, a duplicated receipt, or the date of the last clean cycle count.
    """
    return api_get(f"/inventory/ledger/{product_id}")


class InventoryAdjustmentInput(BaseModel):
    product_id: int = Field(description="The product's numeric identifier")
    bin_id: str = Field(description="The storage bin to correct, e.g. 'A1-01'")
    new_quantity: int = Field(ge=0, description="The correct absolute quantity for this bin")
    reason: str = Field(description="Why the adjustment is being made, for the audit trail")
    incident_id: str = Field(default="", description="Incident this adjustment belongs to")


@tool("adjust_inventory", args_schema=InventoryAdjustmentInput)
def adjust_inventory(product_id: int, bin_id: str, new_quantity: int, reason: str,
                     incident_id: str = ""):
    """Correct the system stock for a product in a bin to its true quantity.

    This is a real mutation: it writes the bin quantity, appends a stock ledger
    entry, updates the product's global on-hand figure, and records a reversible
    action. Only call it once you have confirmed the true quantity, and pass the
    physically counted value as new_quantity.
    """
    return api_post("/inventory/adjust", {
        "product_id": product_id,
        "bin_id": bin_id,
        "new_quantity": new_quantity,
        "reason": reason,
        "incident_id": incident_id or None,
        "agent_name": "Smart_Stock_Agent",
    })


class InventoryTransferInput(BaseModel):
    product_id: int = Field(description="The product's numeric identifier")
    from_bin_id: str = Field(description="Bin to take stock from, e.g. 'C1-01'")
    to_bin_id: str = Field(description="Pick-face or destination bin, e.g. 'A1-01'")
    quantity: int = Field(gt=0, description="Units to move")
    reason: str = Field(description="Why the transfer is being made")
    incident_id: str = Field(default="", description="Incident this transfer belongs to")


@tool("transfer_stock", args_schema=InventoryTransferInput)
def transfer_stock(product_id: int, from_bin_id: str, to_bin_id: str, quantity: int,
                   reason: str, incident_id: str = ""):
    """Move on-hand quantity between bins in the same warehouse.

    Use this to replenish an empty pick face from overflow or reserve bins.
    Same-warehouse only. Does not change total plant on-hand.
    """
    return api_post("/inventory/transfer", {
        "product_id": product_id,
        "from_bin_id": from_bin_id,
        "to_bin_id": to_bin_id,
        "quantity": quantity,
        "reason": reason,
        "incident_id": incident_id or None,
        "agent_name": "Smart_Stock_Agent",
    })
