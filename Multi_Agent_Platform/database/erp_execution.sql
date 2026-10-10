-- System of Execution: simulated ERP + transaction spine.
-- Safe to re-run (IF NOT EXISTS). Does not drop Northwind tables.

SET search_path TO scm, public;

ALTER TABLE actions ADD COLUMN IF NOT EXISTS transaction_id UUID;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS transaction_id UUID;
ALTER TABLE agent_runs ALTER COLUMN incident_id DROP NOT NULL;

CREATE TABLE IF NOT EXISTS plants (
    plant_id TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    country  TEXT
);

CREATE TABLE IF NOT EXISTS vendors (
    vendor_id    TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    country      TEXT,
    incoterms    TEXT NOT NULL DEFAULT 'FOB',
    payment_terms TEXT NOT NULL DEFAULT 'NET30',
    moq          INTEGER NOT NULL DEFAULT 1,
    lead_time_days INTEGER NOT NULL DEFAULT 7,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS materials (
    material_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    uom         TEXT NOT NULL DEFAULT 'EA',
    last_price  NUMERIC(12,2) NOT NULL DEFAULT 0,
    gl_account  TEXT
);

CREATE TABLE IF NOT EXISTS cost_centers (
    cost_center TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    budget_usd  NUMERIC(12,2) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gl_accounts (
    gl_account TEXT PRIMARY KEY,
    name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS routing_guidelines (
    plant_id     TEXT NOT NULL REFERENCES plants(plant_id),
    region       TEXT NOT NULL,
    incoterms    TEXT NOT NULL DEFAULT 'FOB',
    default_mode TEXT NOT NULL DEFAULT 'ROAD',
    PRIMARY KEY (plant_id, region)
);

CREATE TABLE IF NOT EXISTS execution_policy (
    policy_id            TEXT PRIMARY KEY,
    price_deviation_pct  NUMERIC(6,2) NOT NULL DEFAULT 5,
    date_slip_days       INTEGER NOT NULL DEFAULT 5,
    approval_amount_usd  NUMERIC(12,2) NOT NULL DEFAULT 5000
);

CREATE TABLE IF NOT EXISTS approval_rules (
    rule_id     TEXT PRIMARY KEY,
    cost_center TEXT REFERENCES cost_centers(cost_center),
    min_amount  NUMERIC(12,2) NOT NULL DEFAULT 0,
    must_hitl   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS execution_transactions (
    transaction_id UUID PRIMARY KEY,
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    pr_id          TEXT,
    stage          TEXT NOT NULL DEFAULT 'INTENT',
    status         TEXT NOT NULL DEFAULT 'OPEN',
    context        JSONB NOT NULL DEFAULT '{}'::jsonb,
    halt_reason    TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT now(),
    updated_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transaction_events (
    event_id       BIGSERIAL PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    event          TEXT NOT NULL,
    detail         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchase_requisitions (
    pr_id          TEXT PRIMARY KEY,
    transaction_id UUID REFERENCES execution_transactions(transaction_id),
    material_id    TEXT NOT NULL REFERENCES materials(material_id),
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    plant_id       TEXT NOT NULL REFERENCES plants(plant_id),
    cost_center    TEXT NOT NULL REFERENCES cost_centers(cost_center),
    need_by        DATE NOT NULL,
    status         TEXT NOT NULL DEFAULT 'OPEN',
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rfqs (
    rfq_id         TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    pr_id          TEXT NOT NULL REFERENCES purchase_requisitions(pr_id),
    vendor_id      TEXT NOT NULL REFERENCES vendors(vendor_id),
    status         TEXT NOT NULL DEFAULT 'SENT',
    sent_at        TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quotes (
    quote_id       TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    rfq_id         TEXT REFERENCES rfqs(rfq_id),
    vendor_id      TEXT NOT NULL REFERENCES vendors(vendor_id),
    unit_price     NUMERIC(12,2) NOT NULL,
    quantity       INTEGER NOT NULL,
    lead_days      INTEGER NOT NULL DEFAULT 7,
    channel        TEXT NOT NULL DEFAULT 'EMAIL',
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

-- Named exec_* to avoid clashing with the inbound-demo purchase_orders table.
CREATE TABLE IF NOT EXISTS exec_purchase_orders (
    po_id          TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    vendor_id      TEXT NOT NULL REFERENCES vendors(vendor_id),
    material_id    TEXT NOT NULL REFERENCES materials(material_id),
    quantity       INTEGER NOT NULL,
    unit_price     NUMERIC(12,2) NOT NULL,
    plant_id       TEXT NOT NULL,
    cost_center    TEXT NOT NULL,
    need_by        DATE,
    status         TEXT NOT NULL DEFAULT 'ISSUED',
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id     TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    po_id          TEXT REFERENCES exec_purchase_orders(po_id),
    carrier_id     INTEGER,
    carrier_name   TEXT,
    service_level  TEXT NOT NULL DEFAULT 'STANDARD',
    freight_cost   NUMERIC(12,2),
    eta            DATE,
    status         TEXT NOT NULL DEFAULT 'BOOKED',
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tracking_events (
    tracking_id    BIGSERIAL PRIMARY KEY,
    booking_id     TEXT NOT NULL REFERENCES bookings(booking_id),
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    event_code     TEXT NOT NULL,
    detail         TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS goods_receipts_exec (
    gr_id          TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    po_id          TEXT NOT NULL REFERENCES exec_purchase_orders(po_id),
    quantity       INTEGER NOT NULL,
    plant_id       TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS supplier_invoices (
    invoice_id     TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    po_id          TEXT REFERENCES exec_purchase_orders(po_id),
    vendor_id      TEXT NOT NULL,
    quantity       INTEGER NOT NULL,
    unit_price     NUMERIC(12,2) NOT NULL,
    status         TEXT NOT NULL DEFAULT 'CAPTURED',
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS freight_invoices (
    freight_invoice_id TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    booking_id     TEXT REFERENCES bookings(booking_id),
    amount         NUMERIC(12,2) NOT NULL,
    status         TEXT NOT NULL DEFAULT 'CAPTURED',
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoice_matches (
    match_id       TEXT PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES execution_transactions(transaction_id),
    po_id          TEXT,
    gr_id          TEXT,
    invoice_id     TEXT,
    freight_invoice_id TEXT,
    variance_pct   NUMERIC(8,4) NOT NULL DEFAULT 0,
    status         TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS channel_messages (
    message_id     UUID PRIMARY KEY,
    transaction_id UUID REFERENCES execution_transactions(transaction_id),
    direction      TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
    channel        TEXT NOT NULL CHECK (channel IN ('EMAIL','WHATSAPP','EDI','API','PDF')),
    counterparty   TEXT,
    subject        TEXT,
    body           TEXT,
    parsed         JSONB,
    read_at        TIMESTAMP,
    created_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS supplier_memory (
    vendor_id        TEXT NOT NULL REFERENCES vendors(vendor_id),
    transaction_id   UUID,
    response_hours   NUMERIC(8,2),
    reliability_note TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lane_memory (
    region           TEXT NOT NULL,
    carrier_name     TEXT,
    transit_days     INTEGER,
    transaction_id   UUID,
    created_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commercial_memory (
    vendor_id        TEXT NOT NULL,
    material_id      TEXT NOT NULL,
    quoted_price     NUMERIC(12,2),
    paid_price       NUMERIC(12,2),
    transaction_id   UUID,
    created_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exception_memory (
    transaction_id   UUID,
    root_cause       TEXT,
    recovery_action  TEXT,
    approved         BOOLEAN,
    effective        BOOLEAN,
    created_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hitl_requests (
    request_id         UUID PRIMARY KEY,
    transaction_id     UUID REFERENCES execution_transactions(transaction_id) ON DELETE SET NULL,
    workflow           TEXT NOT NULL DEFAULT 'p2p',
    kind               TEXT NOT NULL DEFAULT 'model',
    agent_name         TEXT,
    step               TEXT NOT NULL DEFAULT 'decide',
    question           TEXT NOT NULL,
    options            JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommended_option TEXT,
    rationale          TEXT,
    confidence         DOUBLE PRECISION,
    evidence           JSONB NOT NULL DEFAULT '[]'::jsonb,
    context            JSONB NOT NULL DEFAULT '{}'::jsonb,
    status             TEXT NOT NULL DEFAULT 'open',
    selected_option    TEXT,
    human_note         TEXT,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    answered_at        TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_hitl_open ON hitl_requests(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_hitl_tx ON hitl_requests(transaction_id);

CREATE TABLE IF NOT EXISTS contracted_rates (
    plant_id     TEXT NOT NULL REFERENCES plants(plant_id),
    carrier_id   INTEGER NOT NULL,
    carrier_name TEXT NOT NULL,
    region       TEXT NOT NULL,
    mode         TEXT NOT NULL DEFAULT 'ROAD',
    rate_usd     NUMERIC(12,2) NOT NULL,
    PRIMARY KEY (plant_id, carrier_id, region)
);

CREATE INDEX IF NOT EXISTS idx_tx_stage ON execution_transactions(stage, status);
CREATE INDEX IF NOT EXISTS idx_channel_unread ON channel_messages(transaction_id, read_at);
CREATE INDEX IF NOT EXISTS idx_actions_transaction ON actions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_transaction ON agent_runs(transaction_id);

-- If an earlier apply created document FKs against the inbound-demo purchase_orders
-- table, retarget them at exec_purchase_orders.
DELETE FROM tracking_events te
 WHERE booking_id IN (
    SELECT b.booking_id FROM bookings b
    WHERE b.po_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM exec_purchase_orders e WHERE e.po_id = b.po_id)
 );
DELETE FROM freight_invoices fi
 WHERE booking_id IN (
    SELECT b.booking_id FROM bookings b
    WHERE b.po_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM exec_purchase_orders e WHERE e.po_id = b.po_id)
 );
DELETE FROM supplier_invoices si
 WHERE po_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM exec_purchase_orders e WHERE e.po_id = si.po_id);
DELETE FROM goods_receipts_exec gr
 WHERE NOT EXISTS (SELECT 1 FROM exec_purchase_orders e WHERE e.po_id = gr.po_id);
DELETE FROM bookings b
 WHERE po_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM exec_purchase_orders e WHERE e.po_id = b.po_id);
ALTER TABLE bookings DROP CONSTRAINT IF EXISTS bookings_po_id_fkey;
ALTER TABLE bookings ADD CONSTRAINT bookings_po_id_fkey
    FOREIGN KEY (po_id) REFERENCES exec_purchase_orders(po_id);
ALTER TABLE goods_receipts_exec DROP CONSTRAINT IF EXISTS goods_receipts_exec_po_id_fkey;
ALTER TABLE goods_receipts_exec ADD CONSTRAINT goods_receipts_exec_po_id_fkey
    FOREIGN KEY (po_id) REFERENCES exec_purchase_orders(po_id);
ALTER TABLE supplier_invoices DROP CONSTRAINT IF EXISTS supplier_invoices_po_id_fkey;
ALTER TABLE supplier_invoices ADD CONSTRAINT supplier_invoices_po_id_fkey
    FOREIGN KEY (po_id) REFERENCES exec_purchase_orders(po_id);
