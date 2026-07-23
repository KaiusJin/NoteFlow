from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from noteflow_worker.db.repository import ensure_task_constraints
from noteflow_worker.memory.store import MemoryStore


MESSAGE_STATUS_GENERATING = "GENERATING"
MESSAGE_STATUS_COMPLETED = "COMPLETED"
MESSAGE_STATUS_FAILED = "FAILED"


def scrub_nul(value: str | None) -> str | None:
    """executemany bypasses CleanConnection.execute's NUL scrubbing."""
    return value.replace("\x00", "") if isinstance(value, str) else value


@dataclass(frozen=True)
class Citation:
    citation_index: int
    source_domain: str
    source_object_type: str
    source_object_ids: list[str]
    document_id: str
    page_start: int | None
    page_end: int | None
    source_title: str
    evidence_snapshot: str
    retrieval_score: float


class ConversationStore(MemoryStore):
    """Persistence for answer turns: message lifecycle and durable citations.

    Extends MemoryStore so one instance serves both the memory contract and
    the answer-turn contract; DDL stays idempotent and mirrors the Java
    conversation service exactly (either side may start first).
    """

    def ensure_conversation_schema(self) -> None:
        self.ensure_memory_schema()
        with self.connect() as conn:
            for statement in [
                "ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS model_provider VARCHAR(64)",
                "ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS model_name VARCHAR(128)",
                "ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS structured_response_json TEXT",
                "ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS error_message TEXT",
                "ALTER TABLE rag_messages ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
                # Conversation turns are not document-scoped tasks.
                "ALTER TABLE tasks ALTER COLUMN document_id DROP NOT NULL",
            ]:
                conn.execute(statement)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_message_citations (
                  id UUID PRIMARY KEY,
                  message_id UUID NOT NULL REFERENCES rag_messages(id) ON DELETE CASCADE,
                  citation_index INTEGER NOT NULL,
                  source_domain VARCHAR(32) NOT NULL,
                  source_object_type VARCHAR(64) NOT NULL,
                  source_object_ids JSONB NOT NULL,
                  document_id UUID NOT NULL,
                  page_start INTEGER,
                  page_end INTEGER,
                  source_title VARCHAR(500),
                  evidence_snapshot TEXT NOT NULL,
                  retrieval_score DOUBLE PRECISION,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(message_id, citation_index)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_task_targets (
                  task_id UUID PRIMARY KEY,
                  conversation_id UUID NOT NULL,
                  message_id UUID NOT NULL,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_run_steps (
                  id UUID PRIMARY KEY,
                  message_id UUID NOT NULL REFERENCES rag_messages(id) ON DELETE CASCADE,
                  step_index INTEGER NOT NULL,
                  thought TEXT,
                  action_type VARCHAR(32) NOT NULL,
                  tool VARCHAR(128),
                  args_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                  observation TEXT NOT NULL,
                  ok BOOLEAN NOT NULL DEFAULT TRUE,
                  tokens INTEGER NOT NULL DEFAULT 0,
                  latency_ms INTEGER NOT NULL DEFAULT 0,
                  handle_json JSONB,
                  error_message TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(message_id, step_index)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_run_steps_message ON agent_run_steps(message_id, step_index)")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS agent_run_snapshots (
                  message_id UUID PRIMARY KEY REFERENCES rag_messages(id) ON DELETE CASCADE,
                  conversation_id UUID NOT NULL,user_id UUID NOT NULL,question TEXT NOT NULL,
                  status VARCHAR(24) NOT NULL,state_json JSONB NOT NULL,waiting_task_id UUID,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS agent_task_waits (
                  task_id UUID NOT NULL,message_id UUID NOT NULL REFERENCES rag_messages(id) ON DELETE CASCADE,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(task_id,message_id))"""
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_task_waits_task ON agent_task_waits(task_id)")
            ensure_task_constraints(conn)

    def load_message(self, message_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT id, conversation_id, role, status, content_markdown, token_count,
                          metadata_json, created_at
                   FROM rag_messages WHERE id = %s""",
                (message_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Message not found: {message_id}")
        return dict(row)

    def complete_assistant_message(
        self,
        message_id: str,
        content_markdown: str,
        token_count: int,
        provider: str,
        model: str,
        structured_response_json: str,
        citations: list[Citation],
    ) -> None:
        """Atomically fill the placeholder and persist its citation snapshot."""
        with self.connect() as conn:
            updated = conn.execute(
                """UPDATE rag_messages
                   SET status = %s, content_markdown = %s, token_count = %s,
                       model_provider = %s, model_name = %s, structured_response_json = %s,
                       error_message = NULL, completed_at = NOW()
                   WHERE id = %s AND status = %s""",
                (
                    MESSAGE_STATUS_COMPLETED,
                    content_markdown,
                    token_count,
                    provider,
                    model,
                    structured_response_json,
                    message_id,
                    MESSAGE_STATUS_GENERATING,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Assistant message is no longer awaiting generation.")
            for citation in citations:
                conn.execute(
                    """INSERT INTO rag_message_citations (
                         id, message_id, citation_index, source_domain, source_object_type,
                         source_object_ids, document_id, page_start, page_end, source_title,
                         evidence_snapshot, retrieval_score)
                       VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (message_id, citation_index) DO NOTHING""",
                    (
                        str(uuid4()),
                        message_id,
                        citation.citation_index,
                        citation.source_domain,
                        citation.source_object_type,
                        json.dumps(citation.source_object_ids, separators=(",", ":")),
                        citation.document_id,
                        citation.page_start,
                        citation.page_end,
                        citation.source_title[:500],
                        citation.evidence_snapshot,
                        citation.retrieval_score,
                    ),
                )

    def fail_assistant_message(self, message_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE rag_messages
                   SET status = %s, error_message = %s, completed_at = NOW()
                   WHERE id = %s AND status = %s""",
                (MESSAGE_STATUS_FAILED, error[:2000], message_id, MESSAGE_STATUS_GENERATING),
            )

    def checkpoint_assistant_message(self, message_id: str, structured_response_json: str) -> None:
        """Persist an in-flight agent trace without completing the message."""
        with self.connect() as conn:
            conn.execute(
                """UPDATE rag_messages
                   SET structured_response_json = %s
                   WHERE id = %s AND status = %s""",
                (structured_response_json, message_id, MESSAGE_STATUS_GENERATING),
            )

    def checkpoint_agent_run(self, message_id: str, structured_response_json: str, steps: list[object]) -> None:
        """Persist the compact message trace plus step rows for eval/observability.

        Steps are append-only within a run, so this upserts by (message_id,
        step_index) in one executemany round trip instead of deleting and
        re-inserting every row on each checkpoint. The targeted DELETE only
        clears stale higher-index rows left by a previous retried attempt.
        """
        with self.connect() as conn:
            conn.execute(
                """UPDATE rag_messages
                   SET structured_response_json = %s
                   WHERE id = %s AND status = %s""",
                (structured_response_json, message_id, MESSAGE_STATUS_GENERATING),
            )
            conn.execute(
                "DELETE FROM agent_run_steps WHERE message_id = %s AND step_index >= %s",
                (message_id, len(steps)),
            )
            if not steps:
                return
            rows = [
                (
                    str(uuid4()),
                    message_id,
                    step.step_index,
                    scrub_nul(step.thought),
                    step.action_type,
                    step.tool,
                    scrub_nul(json.dumps(step.args or {}, separators=(",", ":"))),
                    scrub_nul(step.observation),
                    step.ok,
                    step.tokens,
                    step.latency_ms,
                    scrub_nul(json.dumps(step.handle, separators=(",", ":"))) if step.handle else None,
                    scrub_nul(step.error),
                )
                for step in steps
            ]
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO agent_run_steps (
                         id, message_id, step_index, thought, action_type, tool, args_json,
                         observation, ok, tokens, latency_ms, handle_json, error_message)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s)
                       ON CONFLICT (message_id, step_index) DO UPDATE SET
                         thought = EXCLUDED.thought,
                         action_type = EXCLUDED.action_type,
                         tool = EXCLUDED.tool,
                         args_json = EXCLUDED.args_json,
                         observation = EXCLUDED.observation,
                         ok = EXCLUDED.ok,
                         tokens = EXCLUDED.tokens,
                         latency_ms = EXCLUDED.latency_ms,
                         handle_json = EXCLUDED.handle_json,
                         error_message = EXCLUDED.error_message""",
                    rows,
                )

    def bind_task_target(self, task_id: str, conversation_id: str, message_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO conversation_task_targets (task_id, conversation_id, message_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (task_id) DO UPDATE
                   SET conversation_id = EXCLUDED.conversation_id, message_id = EXCLUDED.message_id""",
                (task_id, conversation_id, message_id),
            )

    def pause_agent_run(self, message_id: str, conversation_id: str, user_id: str, question: str,
                        state_json: str, waiting_task_id: str) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO agent_run_snapshots(
              message_id,conversation_id,user_id,question,status,state_json,waiting_task_id)
              VALUES (%s,%s,%s,%s,'WAITING',%s::jsonb,%s)
              ON CONFLICT(message_id) DO UPDATE SET status='WAITING',state_json=EXCLUDED.state_json,
                waiting_task_id=EXCLUDED.waiting_task_id,updated_at=NOW()""",
                (message_id, conversation_id, user_id, question, state_json, waiting_task_id))
            conn.execute("DELETE FROM agent_task_waits WHERE message_id=%s", (message_id,))
            conn.execute("INSERT INTO agent_task_waits(task_id,message_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                         (waiting_task_id, message_id))

    def load_agent_snapshot(self, message_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("""SELECT message_id,conversation_id,user_id,question,status,state_json,waiting_task_id
              FROM agent_run_snapshots WHERE message_id=%s""", (message_id,)).fetchone()
        if not row:
            raise ValueError("Agent continuation snapshot not found")
        value = dict(row)
        if isinstance(value.get("state_json"), str):
            value["state_json"] = json.loads(value["state_json"])
        return value

    def create_resume_tasks(self, completed_task_id: str) -> list[dict]:
        """Atomically turn artifact-completion subscriptions into resume tasks."""
        created = []
        with self.connect() as conn:
            rows = conn.execute("""SELECT w.message_id,s.conversation_id,s.user_id
              FROM agent_task_waits w JOIN tasks t ON t.id=w.task_id AND t.status IN ('COMPLETED','FAILED')
              JOIN agent_run_snapshots s ON s.message_id=w.message_id
              JOIN rag_messages m ON m.id=s.message_id
              WHERE w.task_id=%s AND s.status='WAITING' AND m.status='GENERATING'
              FOR UPDATE OF s""", (completed_task_id,)).fetchall()
            for row in rows:
                task_id = str(uuid4())
                conn.execute("""INSERT INTO tasks(id,document_id,user_id,task_type,status,current_step,
                  progress,retry_count,priority,created_at,updated_at)
                  VALUES (%s,NULL,%s,'RESUME_AGENT_RUN','PENDING','UPLOADED',0,0,0,NOW(),NOW())""",
                  (task_id, row["user_id"]))
                conn.execute("""INSERT INTO conversation_task_targets(task_id,conversation_id,message_id)
                  VALUES (%s,%s,%s)""", (task_id, row["conversation_id"], row["message_id"]))
                conn.execute("UPDATE agent_run_snapshots SET status='QUEUED',updated_at=NOW() WHERE message_id=%s",
                             (row["message_id"],))
                conn.execute("DELETE FROM agent_task_waits WHERE task_id=%s AND message_id=%s",
                             (completed_task_id, row["message_id"]))
                created.append({"task_id": task_id, "user_id": str(row["user_id"]),
                                "conversation_id": str(row["conversation_id"]), "message_id": str(row["message_id"])})
        return created

    def complete_agent_run(self, message_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE agent_run_snapshots SET status='COMPLETED',waiting_task_id=NULL,updated_at=NOW() WHERE message_id=%s",
                         (message_id,))
            conn.execute("DELETE FROM agent_task_waits WHERE message_id=%s", (message_id,))

    def create_maintenance_task(self, task_id: str, user_id: str) -> None:
        """Persist background work before publishing it to Redis."""
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO tasks (
                     id, document_id, user_id, task_type, status, current_step,
                     progress, retry_count, priority, created_at, updated_at)
                   VALUES (%s, NULL, %s, 'MAINTAIN_CONVERSATION_MEMORY',
                     'PENDING', 'UPLOADED', 0, 0, 2, NOW(), NOW())
                   ON CONFLICT (id) DO NOTHING""",
                (task_id, user_id),
            )

    def recover_stale_answer_tasks(self, stale_after_minutes: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """WITH stale AS (
                     SELECT id FROM tasks
                     WHERE task_type IN ('ANSWER_CONVERSATION_TURN','RESUME_AGENT_RUN') AND status = 'PROCESSING'
                       AND updated_at < NOW() - (%s::text||' minutes')::interval
                     FOR UPDATE SKIP LOCKED)
                   UPDATE tasks t SET status = 'RETRYING', retry_count = retry_count + 1,
                     error_message = 'Recovered stale answer task.', updated_at = NOW()
                   FROM stale WHERE t.id = stale.id
                   RETURNING t.id, t.user_id, t.task_type,
                     (SELECT conversation_id FROM conversation_task_targets c WHERE c.task_id = t.id) conversation_id,
                     (SELECT message_id FROM conversation_task_targets c WHERE c.task_id = t.id) message_id""",
                (stale_after_minutes,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_document_titles(self, document_ids: list[str]) -> dict[str, str]:
        unique_ids = sorted(set(document_ids))
        if not unique_ids:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, title FROM documents WHERE id = ANY(%s::uuid[])",
                (unique_ids,),
            ).fetchall()
        return {str(row["id"]): row["title"] or "" for row in rows}
