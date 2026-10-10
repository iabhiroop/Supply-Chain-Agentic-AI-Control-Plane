-- Read-only warehouse copilot surface.
-- Views only; the copilot role has SELECT here and nothing on scm base tables.
-- Safe to re-run.

CREATE SCHEMA IF NOT EXISTS chat_ro;

CREATE OR REPLACE VIEW chat_ro.v_bin_stock AS
SELECT bs.bin_id,
       bs.product_id,
       p.product_name,
       bs.quantity,
       sb.warehouse_id,
       sb.zone,
       bs.updated_at
FROM scm.bin_stock bs
JOIN scm.storage_bins sb ON sb.bin_id = bs.bin_id
JOIN scm.products p ON p.product_id = bs.product_id;

CREATE OR REPLACE VIEW chat_ro.v_storage_bins AS
SELECT bin_id, warehouse_id, zone, capacity
FROM scm.storage_bins;

CREATE OR REPLACE VIEW chat_ro.v_products AS
SELECT product_id, product_name, units_in_stock, reorder_level, discontinued
FROM scm.products;

CREATE OR REPLACE VIEW chat_ro.v_order_lines AS
SELECT od.order_id, od.product_id, p.product_name, od.quantity, od.unit_price
FROM scm.order_details od
JOIN scm.products p ON p.product_id = od.product_id;

CREATE OR REPLACE VIEW chat_ro.v_shipments AS
SELECT s.shipment_id,
       s.order_id,
       s.status,
       c.carrier_name,
       s.service_level,
       s.origin_warehouse_id,
       s.destination_country,
       s.promised_delivery,
       s.estimated_delivery,
       s.freight_cost,
       CASE WHEN s.promised_delivery IS NULL OR s.estimated_delivery IS NULL THEN NULL
            ELSE EXTRACT(DAY FROM (s.estimated_delivery - s.promised_delivery))::int
       END AS days_late
FROM scm.shipments s
JOIN scm.carriers c ON c.carrier_id = s.carrier_id;

CREATE OR REPLACE VIEW chat_ro.v_pick_context AS
SELECT o.order_id,
       o.customer_id,
       o.ship_country,
       od.product_id,
       p.product_name,
       od.quantity AS ordered_qty,
       bs.bin_id,
       COALESCE(bs.quantity, 0) AS bin_qty,
       s.status AS shipment_status
FROM scm.orders o
JOIN scm.order_details od ON od.order_id = o.order_id
JOIN scm.products p ON p.product_id = od.product_id
LEFT JOIN scm.bin_stock bs ON bs.product_id = od.product_id
LEFT JOIN LATERAL (
    SELECT status FROM scm.shipments sh
    WHERE sh.order_id = o.order_id
    ORDER BY sh.created_at DESC
    LIMIT 1
) s ON TRUE;

CREATE OR REPLACE VIEW chat_ro.v_stock_ledger AS
SELECT transaction_id, product_id, bin_id, transaction_type, quantity, reason, reference, created_at
FROM scm.stock_ledger;

CREATE OR REPLACE VIEW chat_ro.v_orders AS
SELECT order_id, customer_id, order_date, required_date, shipped_date,
       ship_city, ship_country, freight
FROM scm.orders;

CREATE OR REPLACE VIEW chat_ro.v_customers AS
SELECT customer_id, company_name, contact_name, city, country, phone, email
FROM scm.customers;

CREATE OR REPLACE VIEW chat_ro.v_sla_terms AS
SELECT ct.customer_id, st.metric, st.threshold_days, st.penalty_pct, st.notes
FROM scm.contracts ct
JOIN scm.sla_terms st ON st.contract_id = ct.contract_id;

CREATE OR REPLACE VIEW chat_ro.v_carrier_rates AS
SELECT c.carrier_name, cr.region, cr.base_rate, cr.expedited_rate,
       cr.base_transit_days, cr.expedited_transit_days, cr.capacity_available
FROM scm.carrier_rates cr
JOIN scm.carriers c ON c.carrier_id = cr.carrier_id;

CREATE OR REPLACE VIEW chat_ro.v_incidents AS
SELECT incident_id, event_type, status, severity, order_id, product_id,
       resolution_summary, created_at, resolved_at
FROM scm.incidents;

CREATE OR REPLACE VIEW chat_ro.v_inbound_asns AS
SELECT a.asn_id, a.po_id, a.supplier_id, s.company_name AS supplier_name,
       a.status, a.expected_bin_id, l.product_id, p.product_name,
       l.expected_qty, l.counted_qty
FROM scm.inbound_asns a
JOIN scm.suppliers s ON s.supplier_id = a.supplier_id
JOIN scm.inbound_asn_lines l ON l.asn_id = a.asn_id
JOIN scm.products p ON p.product_id = l.product_id;

CREATE OR REPLACE VIEW chat_ro.v_purchase_order_lines AS
SELECT l.po_id, l.line_no, l.product_id, p.product_name, l.quantity, po.supplier_id
FROM scm.purchase_order_lines l
JOIN scm.purchase_orders po ON po.po_id = l.po_id
JOIN scm.products p ON p.product_id = l.product_id;

CREATE OR REPLACE VIEW chat_ro.v_detected_signals AS
SELECT signal_id, signal_type, business_key, status, incident_id, created_at
FROM scm.detected_signals;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scm_chat_reader') THEN
        CREATE ROLE scm_chat_reader NOLOGIN;
    END IF;
END$$;

GRANT CONNECT ON DATABASE logistics_scm TO scm_chat_reader;
GRANT USAGE ON SCHEMA chat_ro TO scm_chat_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA chat_ro TO scm_chat_reader;
REVOKE ALL ON SCHEMA scm FROM scm_chat_reader;
GRANT scm_chat_reader TO CURRENT_USER;
