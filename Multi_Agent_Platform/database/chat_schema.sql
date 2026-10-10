-- Additive chat-layer tables for an already-built logistics_scm database.
-- Safe to re-run. Full rebuilds pick these up from schema.sql instead.

CREATE TABLE IF NOT EXISTS scm.chat_sessions (
    session_id         UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    user_id            TEXT NOT NULL DEFAULT 'operator',
    channel            TEXT NOT NULL DEFAULT 'api',
    incident_id        UUID REFERENCES scm.incidents(incident_id),
    pending_proposal   JSONB,
    created_at         TIMESTAMP NOT NULL DEFAULT now(),
    updated_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scm.chat_messages (
    message_id         BIGSERIAL PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES scm.chat_sessions(session_id),
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    intent             TEXT,
    content            TEXT NOT NULL,
    cards              JSONB,
    created_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON scm.chat_messages(session_id, created_at);
