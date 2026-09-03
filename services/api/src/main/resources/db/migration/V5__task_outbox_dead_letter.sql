-- Terminal state for outbox events that exceeded their retry budget:
-- dead_letter_at IS NOT NULL marks a FAILED event that the scheduler skips.
ALTER TABLE task_outbox ADD COLUMN IF NOT EXISTS dead_letter_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_task_outbox_dead_letter
    ON task_outbox(dead_letter_at)
    WHERE dead_letter_at IS NOT NULL;
