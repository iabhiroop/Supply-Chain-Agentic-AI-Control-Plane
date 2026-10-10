-- Durable agent memory (prompts, turns, blackboard, experience, A2A messages).
-- Additive. Safe to re-run.

SET search_path TO scm, public;

ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS prompt_id TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS prompt_version TEXT;

CREATE TABLE IF NOT EXISTS agent_prompt_versions (
    prompt_id   TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('charter', 'system')),
    version     TEXT NOT NULL,
    body        TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    workflow    TEXT NOT NULL DEFAULT 'p2p',
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

ALTER TABLE agent_prompt_versions ADD COLUMN IF NOT EXISTS workflow TEXT NOT NULL DEFAULT 'p2p';

ALTER TABLE agent_prompt_versions
    DROP CONSTRAINT IF EXISTS agent_prompt_versions_agent_name_kind_version_key;

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_prompt_agent_kind_wf_ver
    ON agent_prompt_versions (agent_name, kind, workflow, version);

CREATE TABLE IF NOT EXISTS agent_turns (
    turn_id     UUID PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    run_id      TEXT,
    workflow    TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    prompt_id   TEXT,
    status      TEXT,
    summary     TEXT,
    findings    JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence    JSONB NOT NULL DEFAULT '[]'::jsonb,
    tool_calls  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_turns_trace ON agent_turns (trace_id, created_at);

CREATE TABLE IF NOT EXISTS blackboard_facts (
    trace_id     TEXT NOT NULL,
    fact_key     TEXT NOT NULL,
    value        JSONB NOT NULL,
    source_agent TEXT NOT NULL,
    evidence     TEXT,
    updated_at   TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (trace_id, fact_key)
);

CREATE TABLE IF NOT EXISTS experience_summaries (
    experience_id UUID PRIMARY KEY,
    flow          TEXT NOT NULL,
    vendor_id     TEXT,
    material_id   TEXT,
    po_id         TEXT,
    pr_id         TEXT,
    trace_id      TEXT,
    summary       TEXT NOT NULL,
    outcome       JSONB NOT NULL DEFAULT '{}'::jsonb,
    prompt_ids    TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_experience_vendor_flow ON experience_summaries (vendor_id, flow, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experience_po ON experience_summaries (po_id, flow, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experience_trace ON experience_summaries (trace_id);

CREATE TABLE IF NOT EXISTS agent_messages (
    message_id UUID PRIMARY KEY,
    trace_id   TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_trace ON agent_messages (trace_id, created_at);
