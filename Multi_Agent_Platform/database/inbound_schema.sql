-- Additive ops extensions: detector inbox + inbound ASN/GR.
-- Safe to re-run (IF NOT EXISTS). Applied after schema.sql on rebuild
-- and by database/apply_inbound_schema.py on an existing database.

SET search_path TO scm, public;

ALTER TABLE communications ADD COLUMN IF NOT EXISTS party TEXT NOT NULL DEFAULT 'customer';

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id       TEXT PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    status      TEXT NOT NULL DEFAULT 'OPEN',
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    po_id      TEXT NOT NULL REFERENCES purchase_orders(po_id),
    line_no    INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (po_id, line_no)
);

CREATE TABLE IF NOT EXISTS inbound_asns (
    asn_id          TEXT PRIMARY KEY,
    po_id           TEXT REFERENCES purchase_orders(po_id),
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    status          TEXT NOT NULL DEFAULT 'IN_TRANSIT',
    expected_bin_id TEXT REFERENCES storage_bins(bin_id),
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inbound_asn_lines (
    asn_id        TEXT NOT NULL REFERENCES inbound_asns(asn_id),
    line_no       INTEGER NOT NULL,
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    expected_qty  INTEGER NOT NULL CHECK (expected_qty >= 0),
    counted_qty   INTEGER,
    PRIMARY KEY (asn_id, line_no)
);

CREATE TABLE IF NOT EXISTS goods_receipts (
    receipt_id  BIGSERIAL PRIMARY KEY,
    asn_id      TEXT NOT NULL REFERENCES inbound_asns(asn_id),
    product_id  INTEGER NOT NULL REFERENCES products(product_id),
    quantity    INTEGER NOT NULL,
    bin_id      TEXT NOT NULL REFERENCES storage_bins(bin_id),
    incident_id UUID REFERENCES incidents(incident_id),
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS detected_signals (
    signal_id     UUID PRIMARY KEY,
    tenant_id     TEXT NOT NULL DEFAULT 'default',
    signal_type   TEXT NOT NULL,
    business_key  TEXT NOT NULL,
    entity        JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_payload JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'CONFIRMED', 'DISMISSED')),
    incident_id   UUID REFERENCES incidents(incident_id),
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    updated_at    TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, business_key)
);

CREATE INDEX IF NOT EXISTS idx_detected_signals_status ON detected_signals(status, created_at DESC);
