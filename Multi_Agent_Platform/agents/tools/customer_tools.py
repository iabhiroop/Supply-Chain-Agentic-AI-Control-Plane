"""Customer / CRM tools for the Issue Resolution agent."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.tools.client import api_get, api_post


class OrderInput(BaseModel):
    order_id: int = Field(description="The customer order's numeric identifier")


@tool("get_customer_by_order", args_schema=OrderInput)
def get_customer_by_order(order_id: int):
    """Get the customer and ship-to details for an order.

    Includes the real contact name and email address on file. Always use the
    email returned here; never guess or construct an address.
    """
    return api_get(f"/customers/{order_id}")


@tool("get_sla_terms", args_schema=OrderInput)
def get_sla_terms(order_id: int):
    """Get the contractual SLA terms that apply to an order.

    Use this before promising anything or mentioning compensation: it gives the
    agreed delivery threshold, the notification obligation, and any penalty. Do
    not offer credits or commitments that are not in these terms.
    """
    return api_get(f"/customers/{order_id}/sla")


@tool("get_order_lines", args_schema=OrderInput)
def get_order_lines(order_id: int):
    """Get the line items on an order, with product names and quantities.

    Use this to describe accurately which items are affected.
    """
    return api_get(f"/orders/{order_id}/lines")


class EmailInput(BaseModel):
    to_email: str = Field(description="Recipient address from a tool, or the internal warehouse address")
    subject: str = Field(description="Subject line")
    body: str = Field(description="Full message body")
    order_id: int = Field(default=0, description="Order this message concerns")
    incident_id: str = Field(default="", description="Incident this message belongs to")
    party: str = Field(default="customer", description="customer, supplier, or internal")


@tool("send_email", args_schema=EmailInput)
def send_email(to_email: str, subject: str, body: str, order_id: int = 0,
               incident_id: str = "", party: str = "customer"):
    """Log an email to a customer, supplier, or internal mailbox. Not actually sent.

    Only state facts confirmed by tool results. Set party to match the audience.
    """
    return api_post("/communications/email", {
        "to_email": to_email,
        "subject": subject,
        "body": body,
        "order_id": order_id or None,
        "incident_id": incident_id or None,
        "party": party or "customer",
    })
