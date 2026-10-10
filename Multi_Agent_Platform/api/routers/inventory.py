"""Inventory / WMS endpoints backed by PostgreSQL."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.database.db import fetch_all, get_conn, record_action
from api.database.schemas import (
    BinStockResponse,
    InventoryAdjustmentRequest,
    InventoryAdjustmentResponse,
    InventoryTransferRequest,
    InventoryTransferResponse,
    LedgerEntryResponse,
)

router = APIRouter(prefix="/api/inventory", tags=["Inventory"])


@router.get("/bin/{product_id}", response_model=list[BinStockResponse])
def get_bin_stock(product_id: int):
    """Current stock for a product across every storage bin.

    Used by the Smart Stock Adjustment agent to see what the system believes is
    on hand and where it is physically located.
    """
    return fetch_all(
        """
        SELECT bs.bin_id, bs.product_id, p.product_name, bs.quantity,
               sb.warehouse_id, sb.zone
        FROM bin_stock bs
        JOIN storage_bins sb ON sb.bin_id = bs.bin_id
        JOIN products p ON p.product_id = bs.product_id
        WHERE bs.product_id = %s
        ORDER BY bs.bin_id
        """,
        (product_id,),
    )


@router.get("/ledger/{product_id}", response_model=list[LedgerEntryResponse])
def get_stock_ledger(product_id: int):
    """Full movement history for a product, newest first.

    Used to audit past movements and find the root cause of a discrepancy.
    """
    return fetch_all(
        """
        SELECT transaction_id, product_id, bin_id, transaction_type, quantity,
               reason, reference, created_at
        FROM stock_ledger
        WHERE product_id = %s
        ORDER BY created_at DESC, transaction_id DESC
        LIMIT 100
        """,
        (product_id,),
    )


@router.post("/adjust", response_model=InventoryAdjustmentResponse)
def adjust_inventory(adj: InventoryAdjustmentRequest):
    """Correct the stock quantity for a product in a bin.

    Runs as one transaction: the bin quantity, the append-only ledger entry, the
    product's global on-hand figure, and the reversible action record all commit
    together or not at all.
    """
    with get_conn() as conn:
        current = conn.execute(
            "SELECT quantity FROM bin_stock WHERE product_id = %s AND bin_id = %s FOR UPDATE",
            (adj.product_id, adj.bin_id),
        ).fetchone()
        if current is None:
            raise HTTPException(
                status_code=404,
                detail=f"No bin_stock record for product {adj.product_id} in bin {adj.bin_id}",
            )

        previous = current["quantity"]
        delta = adj.new_quantity - previous
        if delta == 0:
            raise HTTPException(
                status_code=409,
                detail=f"Quantity is already {previous}; no adjustment needed.",
            )

        conn.execute(
            "UPDATE bin_stock SET quantity = %s, updated_at = now() "
            "WHERE product_id = %s AND bin_id = %s",
            (adj.new_quantity, adj.product_id, adj.bin_id),
        )
        conn.execute(
            "INSERT INTO stock_ledger (product_id, bin_id, transaction_type, quantity, reason, reference) "
            "VALUES (%s, %s, 'ADJUSTMENT', %s, %s, %s)",
            (adj.product_id, adj.bin_id, delta, adj.reason, adj.incident_id),
        )
        updated = conn.execute(
            "UPDATE products SET units_in_stock = GREATEST(units_in_stock + %s, 0) "
            "WHERE product_id = %s RETURNING units_in_stock",
            (delta, adj.product_id),
        ).fetchone()

        action_id = record_action(
            conn,
            agent_name=adj.agent_name,
            action_type="INVENTORY_ADJUSTMENT",
            payload={
                "product_id": adj.product_id,
                "bin_id": adj.bin_id,
                "new_quantity": adj.new_quantity,
                "delta": delta,
                "reason": adj.reason,
            },
            inverse={
                "endpoint": "/api/inventory/adjust",
                "product_id": adj.product_id,
                "bin_id": adj.bin_id,
                "new_quantity": previous,
                "reason": f"Compensation for action on product {adj.product_id}",
            },
            incident_id=adj.incident_id,
        )

    return InventoryAdjustmentResponse(
        status="success",
        action_id=action_id,
        product_id=adj.product_id,
        bin_id=adj.bin_id,
        previous_quantity=previous,
        new_quantity=adj.new_quantity,
        delta=delta,
        units_in_stock_after=updated["units_in_stock"],
    )


@router.post("/transfer", response_model=InventoryTransferResponse)
def transfer_stock(req: InventoryTransferRequest):
    """Move quantity between bins in the same warehouse (pick-face replenishment).

    Does not change the product's global on-hand figure. Cross-warehouse moves
    are refused so this stays an internal replenishment, not a site transfer.
    """
    if req.from_bin_id == req.to_bin_id:
        raise HTTPException(status_code=400, detail="from_bin_id and to_bin_id must differ")

    with get_conn() as conn:
        bins = conn.execute(
            """
            SELECT bin_id, warehouse_id FROM storage_bins
            WHERE bin_id IN (%s, %s)
            """,
            (req.from_bin_id, req.to_bin_id),
        ).fetchall()
        by_id = {row["bin_id"]: row["warehouse_id"] for row in bins}
        if req.from_bin_id not in by_id or req.to_bin_id not in by_id:
            raise HTTPException(status_code=404, detail="Unknown source or destination bin")
        if by_id[req.from_bin_id] != by_id[req.to_bin_id]:
            raise HTTPException(
                status_code=409,
                detail="Cross-warehouse transfer is not allowed",
            )

        source = conn.execute(
            "SELECT quantity FROM bin_stock WHERE product_id = %s AND bin_id = %s FOR UPDATE",
            (req.product_id, req.from_bin_id),
        ).fetchone()
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=f"No stock of product {req.product_id} in bin {req.from_bin_id}",
            )
        if source["quantity"] < req.quantity:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Bin {req.from_bin_id} has {source['quantity']}, "
                    f"cannot transfer {req.quantity}"
                ),
            )

        dest = conn.execute(
            "SELECT quantity FROM bin_stock WHERE product_id = %s AND bin_id = %s FOR UPDATE",
            (req.product_id, req.to_bin_id),
        ).fetchone()
        dest_qty = dest["quantity"] if dest else 0

        conn.execute(
            "UPDATE bin_stock SET quantity = quantity - %s, updated_at = now() "
            "WHERE product_id = %s AND bin_id = %s",
            (req.quantity, req.product_id, req.from_bin_id),
        )
        if dest is None:
            conn.execute(
                "INSERT INTO bin_stock (bin_id, product_id, quantity) VALUES (%s, %s, %s)",
                (req.to_bin_id, req.product_id, req.quantity),
            )
        else:
            conn.execute(
                "UPDATE bin_stock SET quantity = quantity + %s, updated_at = now() "
                "WHERE product_id = %s AND bin_id = %s",
                (req.quantity, req.product_id, req.to_bin_id),
            )

        ref = req.incident_id or "transfer"
        conn.execute(
            "INSERT INTO stock_ledger (product_id, bin_id, transaction_type, quantity, reason, reference) "
            "VALUES (%s, %s, 'TRANSFER', %s, %s, %s)",
            (req.product_id, req.from_bin_id, -req.quantity, req.reason, ref),
        )
        conn.execute(
            "INSERT INTO stock_ledger (product_id, bin_id, transaction_type, quantity, reason, reference) "
            "VALUES (%s, %s, 'TRANSFER', %s, %s, %s)",
            (req.product_id, req.to_bin_id, req.quantity, req.reason, ref),
        )

        action_id = record_action(
            conn,
            agent_name=req.agent_name,
            action_type="INVENTORY_TRANSFER",
            payload={
                "product_id": req.product_id,
                "from_bin_id": req.from_bin_id,
                "to_bin_id": req.to_bin_id,
                "quantity": req.quantity,
                "reason": req.reason,
            },
            inverse={
                "endpoint": "/api/inventory/transfer",
                "product_id": req.product_id,
                "from_bin_id": req.to_bin_id,
                "to_bin_id": req.from_bin_id,
                "quantity": req.quantity,
                "reason": f"Compensation for transfer of product {req.product_id}",
            },
            incident_id=req.incident_id,
        )

        from_after = source["quantity"] - req.quantity
        to_after = dest_qty + req.quantity

    return InventoryTransferResponse(
        status="success",
        action_id=action_id,
        product_id=req.product_id,
        from_bin_id=req.from_bin_id,
        to_bin_id=req.to_bin_id,
        quantity=req.quantity,
        from_quantity_after=from_after,
        to_quantity_after=to_after,
    )
