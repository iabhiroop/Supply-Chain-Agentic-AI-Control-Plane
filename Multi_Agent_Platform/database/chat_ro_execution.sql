-- Copilot views over the System of Execution tables (manager-heavy).
-- Apply after erp_execution.sql and chat_ro.sql.

CREATE SCHEMA IF NOT EXISTS chat_ro;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scm_chat_reader') THEN
        CREATE ROLE scm_chat_reader NOLOGIN;
    END IF;
END$$;

CREATE OR REPLACE VIEW chat_ro.v_execution_transactions AS
SELECT transaction_id, pr_id, stage, status, halt_reason, created_at, updated_at
FROM scm.execution_transactions;

CREATE OR REPLACE VIEW chat_ro.v_purchase_requisitions AS
SELECT pr_id, transaction_id, material_id, quantity, plant_id, cost_center, need_by, status, created_at
FROM scm.purchase_requisitions;

CREATE OR REPLACE VIEW chat_ro.v_quotes AS
SELECT quote_id, transaction_id, rfq_id, vendor_id, unit_price, quantity, lead_days, channel, created_at
FROM scm.quotes;

CREATE OR REPLACE VIEW chat_ro.v_exec_purchase_orders AS
SELECT po_id, transaction_id, vendor_id, material_id, quantity, unit_price,
       plant_id, cost_center, need_by, status, created_at
FROM scm.exec_purchase_orders;

CREATE OR REPLACE VIEW chat_ro.v_channel_messages AS
SELECT message_id, transaction_id, direction, channel, counterparty, subject,
       read_at, created_at
FROM scm.channel_messages;

CREATE OR REPLACE VIEW chat_ro.v_bookings AS
SELECT booking_id, transaction_id, po_id, carrier_name, freight_cost, eta, status, created_at
FROM scm.bookings;

CREATE OR REPLACE VIEW chat_ro.v_execution_policy AS
SELECT policy_id, price_deviation_pct, date_slip_days, approval_amount_usd
FROM scm.execution_policy;

CREATE OR REPLACE VIEW chat_ro.v_gl_accounts AS
SELECT gl_account, name
FROM scm.gl_accounts;

CREATE OR REPLACE VIEW chat_ro.v_supplier_memory AS
SELECT vendor_id, transaction_id, response_hours, reliability_note, created_at
FROM scm.supplier_memory;

CREATE OR REPLACE VIEW chat_ro.v_lane_memory AS
SELECT region, carrier_name, transit_days, transaction_id, created_at
FROM scm.lane_memory;

CREATE OR REPLACE VIEW chat_ro.v_commercial_memory AS
SELECT vendor_id, material_id, quoted_price, paid_price, transaction_id, created_at
FROM scm.commercial_memory;

GRANT SELECT ON ALL TABLES IN SCHEMA chat_ro TO scm_chat_reader;
