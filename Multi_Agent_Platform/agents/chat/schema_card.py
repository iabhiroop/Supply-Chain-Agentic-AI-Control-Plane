"""View allowlists and the compact schema card injected into the local model."""

from __future__ import annotations

from enum import Enum

WORKER_VIEWS = frozenset({
    "v_bin_stock",
    "v_storage_bins",
    "v_products",
    "v_order_lines",
    "v_shipments",
    "v_pick_context",
    "v_execution_transactions",
    "v_purchase_requisitions",
    "v_quotes",
    "v_channel_messages",
    "v_bookings",
})

MANAGER_EXTRA = frozenset({
    "v_stock_ledger",
    "v_orders",
    "v_customers",
    "v_sla_terms",
    "v_carrier_rates",
    "v_incidents",
    "v_inbound_asns",
    "v_purchase_order_lines",
    "v_detected_signals",
    "v_exec_purchase_orders",
    "v_execution_policy",
    "v_gl_accounts",
    "v_supplier_memory",
    "v_lane_memory",
    "v_commercial_memory",
})

SCHEMA_NAME = "chat_ro"

_VIEW_DOCS: dict[str, str] = {
    "v_bin_stock": "bin_id, product_id, product_name, quantity, warehouse_id, zone — on-hand per bin",
    "v_storage_bins": "bin_id, warehouse_id, zone, capacity — bin master",
    "v_products": "product_id, product_name, units_in_stock, reorder_level, discontinued",
    "v_order_lines": "order_id, product_id, product_name, quantity, unit_price",
    "v_shipments": "order_id, status, carrier_name, service_level, destination_country, promised_delivery, estimated_delivery, days_late",
    "v_pick_context": "order_id, customer_id, ship_country, product_id, product_name, ordered_qty, bin_id, bin_qty, shipment_status — pick-face snapshot",
    "v_stock_ledger": "product_id, bin_id, transaction_type, quantity, reason, created_at — movement history",
    "v_orders": "order_id, customer_id, order_date, required_date, ship_city, ship_country",
    "v_customers": "customer_id, company_name, contact_name, city, country, email",
    "v_sla_terms": "customer_id, metric, threshold_days, penalty_pct, notes",
    "v_carrier_rates": "carrier_name, region, base_rate, expedited_rate, capacity_available",
    "v_incidents": "incident_id, event_type, status, order_id, product_id, resolution_summary, created_at",
    "v_inbound_asns": "asn_id, po_id, supplier_id, supplier_name, status, expected_bin_id, product_id, product_name, expected_qty, counted_qty",
    "v_purchase_order_lines": "po_id, line_no, product_id, product_name, quantity, supplier_id",
    "v_detected_signals": "signal_id, signal_type, business_key, status, incident_id, created_at",
    "v_execution_transactions": "transaction_id, pr_id, stage, status, halt_reason, created_at, updated_at",
    "v_purchase_requisitions": "pr_id, transaction_id, material_id, quantity, plant_id, cost_center, need_by, status",
    "v_quotes": "quote_id, transaction_id, vendor_id, unit_price, quantity, lead_days, channel",
    "v_exec_purchase_orders": "po_id, transaction_id, vendor_id, material_id, quantity, unit_price, plant_id, status",
    "v_channel_messages": "message_id, transaction_id, direction, channel, counterparty, subject, read_at, created_at",
    "v_bookings": "booking_id, transaction_id, po_id, carrier_name, freight_cost, eta, status",
    "v_execution_policy": "policy_id, price_deviation_pct, date_slip_days, approval_amount_usd — manager only",
    "v_gl_accounts": "gl_account, name — manager only",
    "v_supplier_memory": "vendor_id, transaction_id, response_hours, reliability_note, created_at",
    "v_lane_memory": "region, carrier_name, transit_days, transaction_id, created_at",
    "v_commercial_memory": "vendor_id, material_id, quoted_price, paid_price, transaction_id, created_at",
}


class WarehouseRole(str, Enum):
    WAREHOUSE_WORKER = "warehouse_worker"
    WAREHOUSE_MANAGER = "warehouse_manager"


def views_for(role: WarehouseRole | str) -> frozenset[str]:
    name = role.value if isinstance(role, WarehouseRole) else str(role)
    if name == WarehouseRole.WAREHOUSE_MANAGER.value:
        return WORKER_VIEWS | MANAGER_EXTRA
    return WORKER_VIEWS


def qualified_allowlist(role: WarehouseRole | str) -> set[str]:
    """Names the sandbox accepts: v_bin_stock and chat_ro.v_bin_stock."""
    names = views_for(role)
    allowed = set(names)
    for n in names:
        allowed.add(f"{SCHEMA_NAME}.{n}")
    return allowed


def schema_card(role: WarehouseRole | str) -> str:
    lines = [
        f"You may query ONLY these views in schema {SCHEMA_NAME}. Always qualify as {SCHEMA_NAME}.<view>.",
        "SELECT / WITH ... SELECT only. Never INSERT, UPDATE, DELETE, or DDL.",
        "Always include LIMIT 50 or less.",
        "Views:",
    ]
    for name in sorted(views_for(role)):
        lines.append(f"- {SCHEMA_NAME}.{name}: {_VIEW_DOCS[name]}")
    return "\n".join(lines)
