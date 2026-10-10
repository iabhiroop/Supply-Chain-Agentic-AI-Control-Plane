"""Inbound ASN / goods-receipt endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.database.db import fetch_all, fetch_one, get_conn, record_action
from api.database.schemas import (
    AsnLineResponse,
    AsnResponse,
    GoodsReceiptRequest,
    GoodsReceiptResponse,
    PoLineResponse,
    SupplierResponse,
)

router = APIRouter(prefix="/api", tags=["Inbound"])


def _supplier_email(supplier_id: int, company_name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "" for ch in (company_name or "supplier"))[:24]
    return f"purchasing.s{supplier_id}@{slug or 'supplier'}.example"


@router.get("/suppliers/{supplier_id}", response_model=SupplierResponse)
def get_supplier(supplier_id: int):
    row = fetch_one(
        """
        SELECT supplier_id, company_name, contact_name, country, phone
        FROM suppliers WHERE supplier_id = %s
        """,
        (supplier_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown supplier {supplier_id}")
    return SupplierResponse(
        **row,
        email=_supplier_email(supplier_id, row["company_name"]),
    )


@router.get("/inbound/asn/{asn_id}", response_model=AsnResponse)
def get_asn(asn_id: str):
    header = fetch_one(
        """
        SELECT asn_id, po_id, supplier_id, status, expected_bin_id
        FROM inbound_asns WHERE asn_id = %s
        """,
        (asn_id,),
    )
    if header is None:
        raise HTTPException(status_code=404, detail=f"Unknown ASN {asn_id}")
    lines = fetch_all(
        """
        SELECT l.line_no, l.product_id, p.product_name, l.expected_qty, l.counted_qty
        FROM inbound_asn_lines l
        JOIN products p ON p.product_id = l.product_id
        WHERE l.asn_id = %s
        ORDER BY l.line_no
        """,
        (asn_id,),
    )
    return AsnResponse(
        **header,
        lines=[AsnLineResponse(**line) for line in lines],
    )


@router.get("/inbound/po/{po_id}", response_model=list[PoLineResponse])
def get_po_lines(po_id: str):
    rows = fetch_all(
        """
        SELECT l.po_id, l.line_no, l.product_id, p.product_name, l.quantity
        FROM purchase_order_lines l
        JOIN products p ON p.product_id = l.product_id
        WHERE l.po_id = %s
        ORDER BY l.line_no
        """,
        (po_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Unknown PO {po_id}")
    return rows


@router.post("/inbound/goods-receipt", response_model=GoodsReceiptResponse)
def post_goods_receipt(req: GoodsReceiptRequest):
    """Post counted quantity against an ASN line into the receiving bin."""
    with get_conn() as conn:
        asn = conn.execute(
            "SELECT asn_id, expected_bin_id, status FROM inbound_asns WHERE asn_id = %s FOR UPDATE",
            (req.asn_id,),
        ).fetchone()
        if asn is None:
            raise HTTPException(status_code=404, detail=f"Unknown ASN {req.asn_id}")
        line = conn.execute(
            """
            SELECT expected_qty, counted_qty FROM inbound_asn_lines
            WHERE asn_id = %s AND product_id = %s FOR UPDATE
            """,
            (req.asn_id, req.product_id),
        ).fetchone()
        if line is None:
            raise HTTPException(
                status_code=404,
                detail=f"ASN {req.asn_id} has no line for product {req.product_id}",
            )

        conn.execute(
            """
            UPDATE inbound_asn_lines SET counted_qty = %s
            WHERE asn_id = %s AND product_id = %s
            """,
            (req.quantity, req.asn_id, req.product_id),
        )
        conn.execute(
            "UPDATE inbound_asns SET status = 'RECEIVED' WHERE asn_id = %s",
            (req.asn_id,),
        )

        if req.quantity > 0:
            dest = conn.execute(
                "SELECT quantity FROM bin_stock WHERE product_id = %s AND bin_id = %s FOR UPDATE",
                (req.product_id, req.bin_id),
            ).fetchone()
            if dest is None:
                conn.execute(
                    "INSERT INTO bin_stock (bin_id, product_id, quantity) VALUES (%s, %s, %s)",
                    (req.bin_id, req.product_id, req.quantity),
                )
            else:
                conn.execute(
                    "UPDATE bin_stock SET quantity = quantity + %s, updated_at = now() "
                    "WHERE product_id = %s AND bin_id = %s",
                    (req.quantity, req.product_id, req.bin_id),
                )
            conn.execute(
                "UPDATE products SET units_in_stock = units_in_stock + %s WHERE product_id = %s",
                (req.quantity, req.product_id),
            )
            conn.execute(
                "INSERT INTO stock_ledger (product_id, bin_id, transaction_type, quantity, reason, reference) "
                "VALUES (%s, %s, 'RECEIPT', %s, %s, %s)",
                (req.product_id, req.bin_id, req.quantity, "Goods receipt vs ASN", req.asn_id),
            )

        receipt = conn.execute(
            """
            INSERT INTO goods_receipts (asn_id, product_id, quantity, bin_id, incident_id)
            VALUES (%s, %s, %s, %s, %s) RETURNING receipt_id
            """,
            (req.asn_id, req.product_id, req.quantity, req.bin_id, req.incident_id),
        ).fetchone()

        action_id = record_action(
            conn,
            agent_name=req.agent_name,
            action_type="GOODS_RECEIPT",
            payload={
                "asn_id": req.asn_id,
                "product_id": req.product_id,
                "quantity": req.quantity,
                "bin_id": req.bin_id,
                "expected_qty": line["expected_qty"],
            },
            inverse={
                "endpoint": "/api/inbound/goods-receipt",
                "note": "Reverse GR by posting the previous counted_qty",
                "asn_id": req.asn_id,
                "product_id": req.product_id,
                "quantity": line["counted_qty"] if line["counted_qty"] is not None else 0,
                "bin_id": req.bin_id,
            },
            incident_id=req.incident_id,
        )

    return GoodsReceiptResponse(
        status="success",
        action_id=action_id,
        receipt_id=receipt["receipt_id"],
        asn_id=req.asn_id,
        product_id=req.product_id,
        quantity=req.quantity,
        bin_id=req.bin_id,
    )
