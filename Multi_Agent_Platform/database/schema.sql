-- =============================================================================
-- Logistics SCM — PostgreSQL schema
-- =============================================================================
-- Layer 2 (Multi-Agent Platform) relational system of record.
-- See plan.md §6 for the relational/graph ownership split: this store owns all
-- quantities, statuses, money and audit. Neo4j holds relationships and memory
-- as a projection of this data.
--
-- Naming: snake_case throughout (Northwind's PascalCase and its "Order Details"
-- table with a space are normalised during migration).

DROP SCHEMA IF EXISTS scm CASCADE;
CREATE SCHEMA scm;
SET search_path TO scm;

-- -----------------------------------------------------------------------------
-- ERP core (migrated from Northwind)
-- -----------------------------------------------------------------------------

CREATE TABLE customers (
    customer_id   TEXT PRIMARY KEY,
    company_name  TEXT NOT NULL,
    contact_name  TEXT,
    contact_title TEXT,
    address       TEXT,
    city          TEXT,
    region        TEXT,
    postal_code   TEXT,
    country       TEXT,
    phone         TEXT,
    fax           TEXT,
    -- Northwind has no email address. Synthesised deterministically during
    -- migration so the Issue Resolution agent never has to invent one.
    email         TEXT NOT NULL
);

CREATE TABLE suppliers (
    supplier_id   INTEGER PRIMARY KEY,
    company_name  TEXT NOT NULL,
    contact_name  TEXT,
    contact_title TEXT,
    city          TEXT,
    region        TEXT,
    country       TEXT,
    phone         TEXT,
    -- Synthetic reliability signals for the future Supply Risk agent.
    avg_lead_time_days   INTEGER,
    lead_time_stddev_days NUMERIC(5,2),
    on_time_delivery_pct NUMERIC(5,2)
);

CREATE TABLE categories (
    category_id   INTEGER PRIMARY KEY,
    category_name TEXT NOT NULL,
    description   TEXT
);

CREATE TABLE products (
    product_id       INTEGER PRIMARY KEY,
    product_name     TEXT NOT NULL,
    supplier_id      INTEGER REFERENCES suppliers(supplier_id),
    category_id      INTEGER REFERENCES categories(category_id),
    quantity_per_unit TEXT,
    unit_price       NUMERIC(12,2),
    units_in_stock   INTEGER NOT NULL DEFAULT 0,
    units_on_order   INTEGER NOT NULL DEFAULT 0,
    reorder_level    INTEGER NOT NULL DEFAULT 0,
    discontinued     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE employees (
    employee_id INTEGER PRIMARY KEY,
    last_name   TEXT,
    first_name  TEXT,
    title       TEXT,
    city        TEXT,
    country     TEXT,
    home_phone  TEXT,
    reports_to  INTEGER
);

CREATE TABLE orders (
    order_id         INTEGER PRIMARY KEY,
    customer_id      TEXT REFERENCES customers(customer_id),
    employee_id      INTEGER,
    order_date       TIMESTAMP,
    required_date    TIMESTAMP,
    shipped_date     TIMESTAMP,
    ship_via         INTEGER,
    freight          NUMERIC(12,2),
    ship_name        TEXT,
    ship_address     TEXT,
    ship_city        TEXT,
    ship_region      TEXT,
    ship_postal_code TEXT,
    ship_country     TEXT
);

CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE TABLE order_details (
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    unit_price NUMERIC(12,2) NOT NULL,
    quantity   INTEGER NOT NULL,
    discount   NUMERIC(5,4) NOT NULL DEFAULT 0,
    PRIMARY KEY (order_id, product_id)
);

CREATE INDEX idx_order_details_product ON order_details(product_id);

-- -----------------------------------------------------------------------------
-- WMS extensions (synthetic)
-- -----------------------------------------------------------------------------

CREATE TABLE warehouses (
    warehouse_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    city         TEXT,
    country      TEXT
);

CREATE TABLE storage_bins (
    bin_id       TEXT PRIMARY KEY,
    warehouse_id TEXT NOT NULL REFERENCES warehouses(warehouse_id),
    zone         TEXT,
    capacity     INTEGER NOT NULL
);

CREATE TABLE bin_stock (
    bin_id     TEXT NOT NULL REFERENCES storage_bins(bin_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL CHECK (quantity >= 0),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (bin_id, product_id)
);

-- Append-only audit trail of every stock movement. Never updated or deleted.
CREATE TABLE stock_ledger (
    transaction_id   BIGSERIAL PRIMARY KEY,
    product_id       INTEGER NOT NULL REFERENCES products(product_id),
    bin_id           TEXT NOT NULL REFERENCES storage_bins(bin_id),
    transaction_type TEXT NOT NULL
        CHECK (transaction_type IN ('RECEIPT','ISSUE','ADJUSTMENT','TRANSFER','CYCLE_COUNT')),
    quantity         INTEGER NOT NULL,   -- signed delta
    reason           TEXT,
    reference        TEXT,               -- e.g. incident_id or order_id
    created_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_ledger_product ON stock_ledger(product_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- TMS extensions (synthetic)
-- -----------------------------------------------------------------------------

-- carrier_id deliberately aligns with Northwind shipper_id (1-3) so that
-- orders.ship_via resolves to a real carrier. IDs are explicit, never serial,
-- so re-running the seed cannot drift them (this was blocker B2).
CREATE TABLE carriers (
    carrier_id   INTEGER PRIMARY KEY,
    carrier_name TEXT NOT NULL,
    mode         TEXT NOT NULL DEFAULT 'ROAD',
    phone        TEXT
);

CREATE TABLE carrier_rates (
    rate_id                 SERIAL PRIMARY KEY,
    carrier_id              INTEGER NOT NULL REFERENCES carriers(carrier_id),
    region                  TEXT NOT NULL,
    base_rate               NUMERIC(10,2) NOT NULL,
    expedited_rate          NUMERIC(10,2) NOT NULL,
    base_transit_days       INTEGER NOT NULL,
    expedited_transit_days  INTEGER NOT NULL,
    capacity_available      BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (carrier_id, region)
);

CREATE INDEX idx_rates_region ON carrier_rates(region);

CREATE TABLE shipments (
    shipment_id        SERIAL PRIMARY KEY,
    order_id           INTEGER NOT NULL REFERENCES orders(order_id),
    carrier_id         INTEGER NOT NULL REFERENCES carriers(carrier_id),
    status             TEXT NOT NULL
        CHECK (status IN ('PLANNED','TENDERED','IN_TRANSIT','DELIVERED','DELAYED','CANCELLED')),
    origin_warehouse_id TEXT REFERENCES warehouses(warehouse_id),
    destination_country TEXT,
    service_level      TEXT NOT NULL DEFAULT 'STANDARD',
    promised_delivery  TIMESTAMP,   -- what the customer was told (SLA basis)
    estimated_delivery TIMESTAMP,   -- current best estimate
    freight_cost       NUMERIC(10,2),
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_shipments_order ON shipments(order_id);

-- -----------------------------------------------------------------------------
-- Commercial terms (synthetic) — grounding for customer promises
-- -----------------------------------------------------------------------------

CREATE TABLE contracts (
    contract_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    valid_from  DATE NOT NULL,
    valid_to    DATE NOT NULL,
    incoterms   TEXT
);

CREATE TABLE sla_terms (
    sla_id         SERIAL PRIMARY KEY,
    contract_id    TEXT NOT NULL REFERENCES contracts(contract_id),
    metric         TEXT NOT NULL,
    threshold_days INTEGER,
    penalty_pct    NUMERIC(5,2),
    notes          TEXT
);

-- -----------------------------------------------------------------------------
-- Platform state (incident lifecycle and audit)
-- -----------------------------------------------------------------------------

CREATE TABLE incidents (
    incident_id        UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    event_type         TEXT NOT NULL,
    status             TEXT NOT NULL
        CHECK (status IN ('OPEN','IN_PROGRESS','AWAITING_APPROVAL','RESOLVED','FAILED','CANCELLED')),
    severity           TEXT,
    order_id           INTEGER,
    product_id         INTEGER,
    payload            JSONB NOT NULL,
    resolution_summary TEXT,
    llm_calls          INTEGER NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(10,6) NOT NULL DEFAULT 0,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    resolved_at        TIMESTAMP
);

-- Idempotency: a redelivered event must never open a second incident.
CREATE TABLE idempotency_keys (
    event_key   TEXT PRIMARY KEY,
    incident_id UUID NOT NULL REFERENCES incidents(incident_id),
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE incident_events (
    id          BIGSERIAL PRIMARY KEY,
    incident_id UUID NOT NULL REFERENCES incidents(incident_id),
    event       TEXT NOT NULL,
    detail      JSONB,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE agent_runs (
    run_id       UUID PRIMARY KEY,
    incident_id  UUID NOT NULL REFERENCES incidents(incident_id),
    agent_name   TEXT NOT NULL,
    status       TEXT NOT NULL,
    confidence   NUMERIC(4,3),
    result       JSONB,
    llm_calls    INTEGER NOT NULL DEFAULT 0,
    tool_calls   INTEGER NOT NULL DEFAULT 0,
    cost_usd     NUMERIC(10,6) NOT NULL DEFAULT 0,
    model_used   TEXT,
    started_at   TIMESTAMP NOT NULL DEFAULT now(),
    ended_at     TIMESTAMP
);

-- Outbound customer communications. Logged, never actually sent in non-prod.
CREATE TABLE communications (
    communication_id SERIAL PRIMARY KEY,
    incident_id UUID REFERENCES incidents(incident_id),
    order_id    INTEGER,
    channel     TEXT NOT NULL DEFAULT 'EMAIL',
    to_address  TEXT NOT NULL,
    subject     TEXT,
    body        TEXT,
    status      TEXT NOT NULL DEFAULT 'LOGGED',
    party       TEXT NOT NULL DEFAULT 'customer',
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

-- Every mutating action records how to undo itself (plan §4.1 compensation).
CREATE TABLE actions (
    action_id   UUID PRIMARY KEY,
    incident_id UUID REFERENCES incidents(incident_id),
    agent_name  TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload     JSONB NOT NULL,
    inverse     JSONB,
    status      TEXT NOT NULL DEFAULT 'APPLIED'
        CHECK (status IN ('APPLIED','COMPENSATED','FAILED')),
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_actions_incident ON actions(incident_id, created_at);

-- -----------------------------------------------------------------------------
-- Chat layer (front door; see docs/chat_layer.md)
-- -----------------------------------------------------------------------------

CREATE TABLE chat_sessions (
    session_id         UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    user_id            TEXT NOT NULL DEFAULT 'operator',
    channel            TEXT NOT NULL DEFAULT 'api',
    incident_id        UUID REFERENCES incidents(incident_id),
    pending_proposal   JSONB,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE chat_messages (
    message_id         BIGSERIAL PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES chat_sessions(session_id),
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    intent             TEXT,
    content            TEXT NOT NULL,
    cards              JSONB,
    created_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at);

-- Read-only copilot views live in schema chat_ro (see chat_ro.sql). Applied by
-- build_database.py and database/apply_chat_schema.py so warehouse chat can
-- SELECT without touching scm base tables.
