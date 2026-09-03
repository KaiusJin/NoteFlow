-- PostgreSQL is the task state authority; Redis is the low-latency delivery
-- plane. A worker must atomically own an execution id before running a task.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS execution_id UUID;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS execution_lease_until TIMESTAMPTZ;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_tasks_expired_execution_lease
    ON tasks(execution_lease_until)
    WHERE status = 'PROCESSING';

-- Outbox rows are claimed in a short transaction. Redis I/O happens only
-- after the row lock is released, avoiding network calls inside DB txns.
ALTER TABLE task_outbox ADD COLUMN IF NOT EXISTS claim_token UUID;
ALTER TABLE task_outbox ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_task_outbox_claimable
    ON task_outbox(available_at, created_at)
    WHERE published_at IS NULL AND dead_letter_at IS NULL;
