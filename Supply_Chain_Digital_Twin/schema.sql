-- Wave 1 Digital Twin tables. Additive: IF NOT EXISTS, no DROP, no P2P tables.
-- Requires existing scm.plants / vendors / materials (Layer 2 erp_execution.sql).

SET search_path TO scm, public;

CREATE TABLE IF NOT EXISTS vendor_materials (
    vendor_id      TEXT NOT NULL REFERENCES vendors(vendor_id),
    material_id    TEXT NOT NULL REFERENCES materials(material_id),
    lead_time_days INTEGER NOT NULL DEFAULT 7,
    unit_price     NUMERIC(12,2) NOT NULL,
    is_alternate   BOOLEAN NOT NULL DEFAULT FALSE,
    eligible       BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (vendor_id, material_id)
);

CREATE TABLE IF NOT EXISTS inventory_positions (
    plant_id      TEXT NOT NULL REFERENCES plants(plant_id),
    material_id   TEXT NOT NULL REFERENCES materials(material_id),
    on_hand       INTEGER NOT NULL DEFAULT 0 CHECK (on_hand >= 0),
    safety_stock  INTEGER NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),
    daily_demand  NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK (daily_demand >= 0),
    days_of_cover NUMERIC(10,2) GENERATED ALWAYS AS (
        CASE
            WHEN daily_demand > 0 THEN ROUND((on_hand::numeric / daily_demand), 2)
            ELSE NULL
        END
    ) STORED,
    updated_at    TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (plant_id, material_id)
);

CREATE TABLE IF NOT EXISTS independent_demand (
    plant_id     TEXT NOT NULL REFERENCES plants(plant_id),
    material_id  TEXT NOT NULL REFERENCES materials(material_id),
    qty          INTEGER NOT NULL CHECK (qty > 0),
    horizon_days INTEGER NOT NULL CHECK (horizon_days > 0),
    need_by      DATE,
    PRIMARY KEY (plant_id, material_id)
);

-- Open-book POs not tied to execution_transactions (those require a P2P tx).
CREATE TABLE IF NOT EXISTS twin_purchase_orders (
    po_id              TEXT PRIMARY KEY,
    vendor_id          TEXT NOT NULL REFERENCES vendors(vendor_id),
    material_id        TEXT NOT NULL REFERENCES materials(material_id),
    plant_id           TEXT NOT NULL REFERENCES plants(plant_id),
    quantity           INTEGER NOT NULL CHECK (quantity > 0),
    unit_price         NUMERIC(12,2) NOT NULL,
    promise_date       DATE,
    status             TEXT NOT NULL DEFAULT 'OPEN',
    reallocatable_qty  INTEGER NOT NULL DEFAULT 0 CHECK (reallocatable_qty >= 0),
    created_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS twin_signals (
    signal_id    TEXT PRIMARY KEY,
    signal_type  TEXT NOT NULL,
    entity_id    TEXT,
    headline     TEXT NOT NULL,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_time   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS twin_po_vendor_status ON twin_purchase_orders (vendor_id, status);
CREATE INDEX IF NOT EXISTS twin_signals_type ON twin_signals (signal_type);
