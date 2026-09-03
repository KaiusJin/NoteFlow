"""Authoritative PostgreSQL task execution claims.

Redis delivers wake-up messages at least once. This store decides whether a
particular delivery may execute, renews its database lease, and performs the
retry/terminal transition after an unexpected worker failure.
"""

from __future__ import annotations

from dataclasses import dataclass

from noteflow_worker.db.connection import BaseRepository
from noteflow_worker.queue.redis_queue import TaskPayload


TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


@dataclass(frozen=True)
class TaskExecutionState:
    status: str
    retry_count: int
    execution_id: str | None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class TaskExecutionStore(BaseRepository):
    def claim(self, payload: TaskPayload, lease_seconds: int) -> bool:
        if not payload.lease_id:
            raise ValueError("A Redis lease id is required before claiming a task")
        with self.connect() as conn:
            row = conn.execute(
                """
                UPDATE tasks
                SET status = 'PROCESSING',
                    execution_id = %s,
                    execution_lease_until = NOW() + (%s::text || ' seconds')::interval,
                    last_heartbeat_at = NOW(),
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                  AND user_id = %s
                  AND task_type = %s
                  AND status IN ('PENDING', 'RETRYING')
                RETURNING id
                """,
                (payload.lease_id, max(30, lease_seconds), payload.task_id, payload.user_id, payload.task_type),
            ).fetchone()
        return row is not None

    def state(self, task_id: str) -> TaskExecutionState | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status,retry_count,execution_id FROM tasks WHERE id=%s",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return TaskExecutionState(
            status=str(row["status"]),
            retry_count=int(row["retry_count"] or 0),
            execution_id=str(row["execution_id"]) if row.get("execution_id") else None,
        )

    def renew(self, payload: TaskPayload, lease_seconds: int) -> bool:
        if not payload.lease_id:
            return False
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET execution_lease_until = NOW() + (%s::text || ' seconds')::interval,
                    last_heartbeat_at = NOW()
                WHERE id=%s AND execution_id=%s AND status='PROCESSING'
                """,
                (max(30, lease_seconds), payload.task_id, payload.lease_id),
            )
        return cursor.rowcount == 1

    def retry_or_fail(
        self,
        payload: TaskPayload,
        reason: str,
        max_retries: int,
    ) -> TaskExecutionState | None:
        if not payload.lease_id:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """
                UPDATE tasks
                SET retry_count = retry_count + 1,
                    status = CASE WHEN retry_count + 1 > %s THEN 'FAILED' ELSE 'RETRYING' END,
                    current_step = CASE WHEN retry_count + 1 > %s THEN 'FAILED' ELSE current_step END,
                    progress = CASE WHEN retry_count + 1 > %s THEN 100 ELSE progress END,
                    error_message = %s,
                    completed_at = CASE WHEN retry_count + 1 > %s THEN NOW() ELSE NULL END,
                    execution_id = NULL,
                    execution_lease_until = NULL,
                    last_heartbeat_at = NOW(),
                    updated_at = NOW()
                WHERE id=%s AND execution_id=%s AND status='PROCESSING'
                RETURNING status,retry_count,execution_id
                """,
                (
                    max(0, max_retries),
                    max(0, max_retries),
                    max(0, max_retries),
                    reason[:4000],
                    max(0, max_retries),
                    payload.task_id,
                    payload.lease_id,
                ),
            ).fetchone()
        if not row:
            return self.state(payload.task_id)
        return TaskExecutionState(str(row["status"]), int(row["retry_count"]), None)

    def recover_expired(
        self,
        stale_without_lease_minutes: int,
        max_retries: int,
        limit: int = 100,
    ) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH expired AS (
                  SELECT id
                  FROM tasks
                  WHERE status='PROCESSING'
                    AND (
                      execution_lease_until < NOW()
                      OR (
                        execution_lease_until IS NULL
                        AND updated_at < NOW() - (%s::text || ' minutes')::interval
                      )
                    )
                  ORDER BY COALESCE(execution_lease_until, updated_at)
                  LIMIT %s
                  FOR UPDATE SKIP LOCKED
                )
                UPDATE tasks t
                SET retry_count = retry_count + 1,
                    status = CASE WHEN retry_count + 1 > %s THEN 'FAILED' ELSE 'RETRYING' END,
                    current_step = CASE WHEN retry_count + 1 > %s THEN 'FAILED' ELSE current_step END,
                    progress = CASE WHEN retry_count + 1 > %s THEN 100 ELSE progress END,
                    error_message = CASE
                      WHEN retry_count + 1 > %s THEN 'Execution lease expired and retry budget was exhausted.'
                      ELSE 'Execution lease expired; task was made available for retry.'
                    END,
                    completed_at = CASE WHEN retry_count + 1 > %s THEN NOW() ELSE NULL END,
                    execution_id = NULL,
                    execution_lease_until = NULL,
                    last_heartbeat_at = NOW(),
                    updated_at = NOW()
                FROM expired
                WHERE t.id=expired.id
                RETURNING
                  t.id,t.document_id,t.user_id,t.task_type,t.status,t.retry_count,
                  (SELECT attempt_id FROM study_task_targets s WHERE s.task_id=t.id) attempt_id,
                  (SELECT conversation_id FROM conversation_task_targets c WHERE c.task_id=t.id) conversation_id,
                  (SELECT message_id FROM conversation_task_targets c WHERE c.task_id=t.id) message_id
                """,
                (
                    max(1, stale_without_lease_minutes),
                    max(1, limit),
                    max(0, max_retries),
                    max(0, max_retries),
                    max(0, max_retries),
                    max(0, max_retries),
                    max(0, max_retries),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def ready_retries(self, limit: int = 100) -> list[dict]:
        """Return retry rows that need a Redis wake-up.

        Re-publishing may create duplicate messages if a previous wake-up is
        still queued. The atomic database claim makes duplicates cheap, while
        this sweep prevents Redis downtime from stranding a retry.
        """
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  t.id,t.document_id,t.user_id,t.task_type,t.status,t.retry_count,
                  (SELECT attempt_id FROM study_task_targets s WHERE s.task_id=t.id) attempt_id,
                  (SELECT conversation_id FROM conversation_task_targets c WHERE c.task_id=t.id) conversation_id,
                  (SELECT message_id FROM conversation_task_targets c WHERE c.task_id=t.id) message_id
                FROM tasks t
                WHERE t.status='RETRYING' AND t.execution_id IS NULL
                ORDER BY t.updated_at
                LIMIT %s
                """,
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]
