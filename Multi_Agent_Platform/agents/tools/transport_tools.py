"""Transport / TMS tools for the Tendering and Dispatching agents."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.tools.client import api_get, api_post


class OrderInput(BaseModel):
    order_id: int = Field(description="The customer order's numeric identifier")


@tool("get_shipment", args_schema=OrderInput)
def get_shipment(order_id: int):
    """Get the active shipment for an order.

    Returns the assigned carrier, status, service level, destination, the date
    promised to the customer, the current estimate, and `days_late` (already
    calculated — do not compute dates yourself).
    """
    return api_get(f"/shipments/{order_id}")


class RegionInput(BaseModel):
    region: str = Field(description="Destination region or country, e.g. 'France'")


@tool("get_carrier_rates", args_schema=RegionInput)
def get_carrier_rates(region: str):
    """Get carriers serving a region with rates, transit times and capacity.

    Each option includes base_rate, expedited_rate, base_transit_days,
    expedited_transit_days and capacity_available. A carrier with
    capacity_available=false cannot be booked, however attractive its price.
    Choose the cheapest option that both has capacity and meets the required
    timeline.
    """
    return api_get(f"/carrier_rates/{region}")


class ShipmentTenderInput(BaseModel):
    order_id: int = Field(description="The customer order's numeric identifier")
    new_carrier_id: int = Field(description="carrier_id of the carrier to book")
    new_estimated_delivery: str = Field(
        description="New estimated delivery date, 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'"
    )
    service_level: str = Field(default="EXPEDITED", description="'EXPEDITED' or 'STANDARD'")
    reason: str = Field(default="Re-tendered after disruption", description="Audit reason")
    incident_id: str = Field(default="", description="Incident this booking belongs to")


@tool("tender_shipment", args_schema=ShipmentTenderInput)
def tender_shipment(order_id: int, new_carrier_id: int, new_estimated_delivery: str,
                    service_level: str = "EXPEDITED",
                    reason: str = "Re-tendered after disruption",
                    incident_id: str = ""):
    """Book a new carrier and delivery date for an order's shipment.

    This is a real mutation and is validated: booking a carrier that does not
    serve the destination, or that has no capacity, will be refused with an
    explanation. On success it returns the freight cost and `meets_promise`,
    showing whether the new date still honours the customer's promised date.
    """
    return api_post("/shipments/tender", {
        "order_id": order_id,
        "new_carrier_id": new_carrier_id,
        "new_estimated_delivery": new_estimated_delivery,
        "service_level": service_level,
        "reason": reason,
        "incident_id": incident_id or None,
        "agent_name": "Dispatching_Agent",
    })
