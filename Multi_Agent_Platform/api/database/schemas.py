"""Request/response contracts for the internal ERP/WMS/TMS API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# --- Inventory / WMS ---------------------------------------------------------


class BinStockResponse(BaseModel):
    bin_id: str
    product_id: int
    product_name: str | None = None
    quantity: int
    warehouse_id: str | None = None
    zone: str | None = None


class LedgerEntryResponse(BaseModel):
    transaction_id: int
    product_id: int
    bin_id: str
    transaction_type: str
    quantity: int
    reason: str | None = None
    reference: str | None = None
    created_at: datetime


class InventoryAdjustmentRequest(BaseModel):
    product_id: int
    bin_id: str
    new_quantity: int = Field(ge=0)
    reason: str
    incident_id: str | None = None
    agent_name: str = "Smart_Stock_Agent"


class InventoryAdjustmentResponse(BaseModel):
    status: str
    action_id: str
    product_id: int
    bin_id: str
    previous_quantity: int
    new_quantity: int
    delta: int
    units_in_stock_after: int


class InventoryTransferRequest(BaseModel):
    product_id: int
    from_bin_id: str
    to_bin_id: str
    quantity: int = Field(gt=0)
    reason: str
    incident_id: str | None = None
    agent_name: str = "Smart_Stock_Agent"


class InventoryTransferResponse(BaseModel):
    status: str
    action_id: str
    product_id: int
    from_bin_id: str
    to_bin_id: str
    quantity: int
    from_quantity_after: int
    to_quantity_after: int


# --- Transport / TMS ---------------------------------------------------------


class ShipmentResponse(BaseModel):
    shipment_id: int
    order_id: int
    carrier_id: int
    carrier_name: str
    carrier_mode: str
    status: str
    service_level: str
    origin_warehouse_id: str | None = None
    destination_country: str | None = None
    promised_delivery: datetime | None = None
    estimated_delivery: datetime | None = None
    # Positive when the current estimate misses what the customer was promised.
    days_late: int | None = None
    freight_cost: float | None = None


class CarrierRateResponse(BaseModel):
    carrier_id: int
    carrier_name: str
    mode: str
    region: str
    base_rate: float
    expedited_rate: float
    base_transit_days: int
    expedited_transit_days: int
    capacity_available: bool


class ShipmentTenderRequest(BaseModel):
    order_id: int
    new_carrier_id: int
    new_estimated_delivery: str = Field(
        description="New estimated delivery, ISO 8601 (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)"
    )
    service_level: str = "EXPEDITED"
    reason: str = "Re-tendered after disruption"
    incident_id: str | None = None
    agent_name: str = "Dispatching_Agent"
    # Rollback has to be able to restore the previous state even when that
    # state would fail a forward-booking check (the original carrier may have
    # dropped the load). Only the compensation path may set this, and doing so
    # is recorded in the action log.
    is_compensation: bool = False


class ShipmentTenderResponse(BaseModel):
    status: str
    action_id: str
    order_id: int
    shipment_id: int
    previous_carrier_id: int
    previous_carrier_name: str
    new_carrier_id: int
    new_carrier_name: str
    new_estimated_delivery: datetime
    freight_cost: float | None = None
    meets_promise: bool


# --- Customer / CRM ----------------------------------------------------------


class CustomerResponse(BaseModel):
    customer_id: str
    company_name: str
    contact_name: str | None = None
    email: str
    phone: str | None = None
    country: str | None = None
    ship_name: str | None = None
    ship_city: str | None = None
    ship_region: str | None = None
    ship_country: str | None = None
    order_id: int


class SlaTermResponse(BaseModel):
    contract_id: str
    metric: str
    threshold_days: int | None = None
    penalty_pct: float | None = None
    notes: str | None = None


class OrderLineResponse(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: float


class EmailRequest(BaseModel):
    to_email: str
    subject: str
    body: str
    order_id: int | None = None
    incident_id: str | None = None
    party: str = "customer"


class EmailResponse(BaseModel):
    status: str
    communication_id: int
    to_address: str


class SupplierResponse(BaseModel):
    supplier_id: int
    company_name: str
    contact_name: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str


class AsnLineResponse(BaseModel):
    line_no: int
    product_id: int
    product_name: str | None = None
    expected_qty: int
    counted_qty: int | None = None


class AsnResponse(BaseModel):
    asn_id: str
    po_id: str | None = None
    supplier_id: int
    status: str
    expected_bin_id: str | None = None
    lines: list[AsnLineResponse] = Field(default_factory=list)


class PoLineResponse(BaseModel):
    po_id: str
    line_no: int
    product_id: int
    product_name: str | None = None
    quantity: int


class GoodsReceiptRequest(BaseModel):
    asn_id: str
    product_id: int
    quantity: int = Field(ge=0)
    bin_id: str
    incident_id: str | None = None
    agent_name: str = "Inbound_Receiving_Agent"


class GoodsReceiptResponse(BaseModel):
    status: str
    action_id: str
    receipt_id: int
    asn_id: str
    product_id: int
    quantity: int
    bin_id: str

