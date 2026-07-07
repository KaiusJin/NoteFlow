from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import uuid4

from noteflow_worker.db.repository import ensure_task_constraints
from noteflow_worker.memory.store import MemoryStore


MESSAGE_STATUS_GENERATING = "GENERATING"
MESSAGE_STATUS_COMPLETED = "COMPLETED"
MESSAGE_STATUS_FAILED = "FAILED"


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

    def bind_task_target(self, task_id: str, conversation_id: str, message_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO conversation_task_targets (task_id, conversation_id, message_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (task_id) DO UPDATE
                   SET conversation_id = EXCLUDED.conversation_id, message_id = EXCLUDED.message_id""",
                (task_id, conversation_id, message_id),
            )

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
                     WHERE task_type = 'ANSWER_CONVERSATION_TURN' AND status = 'PROCESSING'
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
