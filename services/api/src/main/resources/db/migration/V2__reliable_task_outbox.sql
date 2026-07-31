CREATE TABLE task_outbox (
    id UUID PRIMARY KEY,
    task_id UUID NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    attempt_id UUID,
    conversation_id UUID,
    message_id UUID,
    created_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE INDEX idx_task_outbox_pending
    ON task_outbox(available_at, created_at)
    WHERE published_at IS NULL;

CREATE INDEX idx_task_outbox_published
    ON task_outbox(published_at)
    WHERE published_at IS NOT NULL;
