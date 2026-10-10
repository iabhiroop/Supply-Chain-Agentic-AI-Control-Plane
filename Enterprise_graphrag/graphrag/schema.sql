CREATE SCHEMA IF NOT EXISTS graphrag;
SET search_path TO graphrag, public;

CREATE TABLE IF NOT EXISTS documents (
    document_id     TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    source_uri      TEXT NOT NULL,
    doc_type        TEXT NOT NULL DEFAULT 'md',
    title           TEXT,
    content_hash    TEXT NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    superseded_by   TEXT,
    current         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, content_hash)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    ordinal         INTEGER NOT NULL,
    heading         TEXT,
    text            TEXT NOT NULL,
    anchor          TEXT,
    embedding       DOUBLE PRECISION[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunks_doc_idx ON chunks (document_id);
CREATE INDEX IF NOT EXISTS chunks_tenant_idx ON chunks (tenant_id);

CREATE TABLE IF NOT EXISTS ingest_jobs (
    job_id          TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    status          TEXT NOT NULL DEFAULT 'QUEUED',
    source          TEXT,
    counts          JSONB NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id         TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    label           TEXT NOT NULL,
    key             TEXT NOT NULL,
    name            TEXT,
    properties      JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid_from      TIMESTAMPTZ,
    valid_to        TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, label, key)
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id         TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    rel_type        TEXT NOT NULL,
    src_id          TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    dst_id          TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    valid_from      TIMESTAMPTZ,
    valid_to        TIMESTAMPTZ,
    provenance      JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (tenant_id, rel_type, src_id, dst_id)
);

CREATE TABLE IF NOT EXISTS communities (
    community_id    TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    level           INTEGER NOT NULL DEFAULT 0,
    summary         TEXT NOT NULL,
    member_ids      TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    query           TEXT,
    relevant        BOOLEAN,
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS query_log (
    log_id          TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    query           TEXT,
    intent          TEXT,
    strategies      TEXT[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
