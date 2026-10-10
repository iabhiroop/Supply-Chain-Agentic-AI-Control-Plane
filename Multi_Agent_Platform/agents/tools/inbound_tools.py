"""Inbound ASN / GR tools for the Inbound Receiving agent."""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.tools.client import api_get, api_post


class AsnInput(BaseModel):
    asn_id: str = Field(description="ASN identifier, e.g. ASN-1001")


class PoInput(BaseModel):
    po_id: str = Field(description="Purchase order identifier, e.g. PO-11")


class SupplierInput(BaseModel):
    supplier_id: int = Field(description="Northwind supplier_id")


class GoodsReceiptInput(BaseModel):
    asn_id: str
    product_id: int
    quantity: int = Field(ge=0, description="Physically counted quantity to post")
    bin_id: str = Field(description="Receiving bin, usually the ASN expected_bin_id")
    incident_id: str = Field(default="")


@tool("get_asn", args_schema=AsnInput)
def get_asn(asn_id: str):
    """Get an inbound ASN header and lines (expected vs counted qty)."""
    return api_get(f"/inbound/asn/{asn_id}")


@tool("get_po_line", args_schema=PoInput)
def get_po_line(po_id: str):
    """Get purchase-order lines the ASN should reconcile against."""
    return api_get(f"/inbound/po/{po_id}")


@tool("get_supplier", args_schema=SupplierInput)
def get_supplier(supplier_id: int):
    """Get supplier contact details including a deterministic purchasing email."""
    return api_get(f"/suppliers/{supplier_id}")


@tool("post_goods_receipt", args_schema=GoodsReceiptInput)
def post_goods_receipt(asn_id: str, product_id: int, quantity: int, bin_id: str,
                       incident_id: str = ""):
    """Post the counted quantity against the ASN into the receiving bin.

    Use the physically counted quantity, including zero. This is a real mutation.
    """
    return api_post("/inbound/goods-receipt", {
        "asn_id": asn_id,
        "product_id": product_id,
        "quantity": quantity,
        "bin_id": bin_id,
        "incident_id": incident_id or None,
        "agent_name": "Inbound_Receiving_Agent",
    })
