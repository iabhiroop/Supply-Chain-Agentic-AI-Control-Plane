-- Layer 4 evaluation store. Isolated schema so scorers never write SCM ERP tables.
CREATE SCHEMA IF NOT EXISTS eval;
SET search_path TO eval, public;

CREATE TABLE IF NOT EXISTS traces (
    trace_id        TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    workflow        TEXT,
    transaction_id  TEXT,
    status          TEXT,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    cost_usd        DOUBLE PRECISION NOT NULL DEFAULT 0,
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    source          TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS spans (
    span_id         TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
    parent_span_id  TEXT,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'internal',
    status          TEXT NOT NULL DEFAULT 'OK',
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    duration_ms     DOUBLE PRECISION,
    model           TEXT,
    prompt_id       TEXT,
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd        DOUBLE PRECISION NOT NULL DEFAULT 0,
    cache_status    TEXT,
    input           JSONB,
    output          JSONB,
    error           TEXT,
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS spans_trace_idx ON spans (trace_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    trace_id        TEXT REFERENCES traces(trace_id) ON DELETE SET NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    workflow        TEXT,
    transaction_id  TEXT,
    agent_version   TEXT,
    status          TEXT,
    summary         TEXT,
    snapshot        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    version_id      TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    version         TEXT NOT NULL,
    immutable       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_id, version)
);

CREATE TABLE IF NOT EXISTS dataset_items (
    item_id         TEXT PRIMARY KEY,
    version_id      TEXT NOT NULL REFERENCES dataset_versions(version_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    input           JSONB NOT NULL,
    expected        JSONB NOT NULL,
    tags            TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id   TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    dataset_version_id TEXT REFERENCES dataset_versions(version_id),
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS experiment_results (
    result_id       TEXT PRIMARY KEY,
    experiment_id   TEXT NOT NULL REFERENCES experiments(experiment_id) ON DELETE CASCADE,
    item_id         TEXT,
    run_id          TEXT,
    passed          BOOLEAN,
    metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scores (
    score_id        TEXT PRIMARY KEY,
    run_id          TEXT,
    trace_id        TEXT,
    scorer          TEXT NOT NULL,
    scorer_version  TEXT NOT NULL,
    metric          TEXT NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    passed          BOOLEAN,
    rationale       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scores_run_idx ON scores (run_id);

CREATE TABLE IF NOT EXISTS judges (
    judge_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    version         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS annotations (
    annotation_id   TEXT PRIMARY KEY,
    run_id          TEXT,
    annotator       TEXT NOT NULL DEFAULT 'operator',
    rubric          JSONB NOT NULL DEFAULT '{}'::jsonb,
    labels          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS annotators (
    annotator_id    TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'annotator'
);

CREATE TABLE IF NOT EXISTS gates (
    gate_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    policy          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS baselines (
    baseline_id     TEXT PRIMARY KEY,
    gate_id         TEXT,
    experiment_id   TEXT,
    metrics         JSONB NOT NULL,
    approved_by     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id        TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    message         TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     TEXT PRIMARY KEY,
    run_id          TEXT,
    source          TEXT,
    rating          INTEGER,
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
