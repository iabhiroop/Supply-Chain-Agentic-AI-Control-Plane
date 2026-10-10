"""Resolve order / product / customer / bin mentions from free text."""

from __future__ import annotations

import re
from typing import Any

from agents.config.db import fetch_all, fetch_one

ORDER_RE = re.compile(r"\b(?:order\s*#?\s*)?(\d{4,6})\b", re.I)
PRODUCT_ID_RE = re.compile(r"\bproduct(?:[_\s]*id)?\s*#?\s*(\d{1,5})\b", re.I)
QTY_RE = re.compile(
    r"\b(?:counted|reported(?:_qty)?|qty|quantity)\s*(?:of\s*|:\s*|=\s*)?(\d+)\b",
    re.I,
)
EMPTY_RE = re.compile(r"\b(empty|zero|nothing|no stock|counted\s*0|qty\s*0)\b", re.I)
BIN_RE = re.compile(r"\b([A-Z]\d{1,2}-\d{2})\b", re.I)


def resolve_entities(text: str) -> dict[str, Any]:
    """Best-effort entity extraction. Ambiguous names are returned, not guessed."""
    found: dict[str, Any] = {
        "order_id": None,
        "product_id": None,
        "product_name": None,
        "bin_id": None,
        "reported_qty": None,
        "customer_id": None,
        "ambiguous": [],
        "evidence": [],
    }

    order_ids = [int(m) for m in ORDER_RE.findall(text)]
    # Prefer IDs that actually exist; Northwind orders start at 10248.
    for oid in order_ids:
        row = fetch_one(
            "SELECT order_id, customer_id FROM orders WHERE order_id = %s", (oid,)
        )
        if row:
            found["order_id"] = row["order_id"]
            found["customer_id"] = row["customer_id"]
            found["evidence"].append(f"order {oid} exists ({row['customer_id']})")
            break

    pid_match = PRODUCT_ID_RE.search(text)
    if pid_match:
        pid = int(pid_match.group(1))
        prod = fetch_one(
            "SELECT product_id, product_name FROM products WHERE product_id = %s", (pid,)
        )
        if prod:
            found["product_id"] = prod["product_id"]
            found["product_name"] = prod["product_name"]
            found["evidence"].append(f"product {pid} is {prod['product_name']}")

    if found["product_id"] is None:
        names = fetch_all("SELECT product_id, product_name FROM products")
        lowered = text.lower()
        hits = [p for p in names if p["product_name"] and p["product_name"].lower() in lowered]
        if len(hits) == 1:
            found["product_id"] = hits[0]["product_id"]
            found["product_name"] = hits[0]["product_name"]
            found["evidence"].append(
                f"matched product name {hits[0]['product_name']} (id {hits[0]['product_id']})"
            )
        elif len(hits) > 1:
            found["ambiguous"].append(
                "product: " + ", ".join(f"{h['product_name']} ({h['product_id']})" for h in hits[:5])
            )

    bin_match = BIN_RE.search(text)
    if bin_match:
        bin_id = bin_match.group(1).upper()
        row = fetch_one("SELECT bin_id FROM storage_bins WHERE bin_id = %s", (bin_id,))
        if row:
            found["bin_id"] = row["bin_id"]
            found["evidence"].append(f"bin {row['bin_id']} exists")

    qty_match = QTY_RE.search(text)
    if qty_match:
        found["reported_qty"] = int(qty_match.group(1))
        found["evidence"].append(f"reported qty {found['reported_qty']}")
    elif EMPTY_RE.search(text):
        found["reported_qty"] = 0
        found["evidence"].append("text implies physically empty (qty 0)")

    if found["order_id"] and found["product_id"] is None:
        lines = fetch_all(
            """
            SELECT od.product_id, p.product_name, od.quantity
            FROM order_details od
            JOIN products p ON p.product_id = od.product_id
            WHERE od.order_id = %s
            ORDER BY od.product_id
            """,
            (found["order_id"],),
        )
        if len(lines) == 1:
            found["product_id"] = lines[0]["product_id"]
            found["product_name"] = lines[0]["product_name"]
            found["evidence"].append(
                f"order has a single line: {lines[0]['product_name']} ({lines[0]['product_id']})"
            )

    return found
