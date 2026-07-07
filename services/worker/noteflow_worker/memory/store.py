from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from noteflow_worker.db.repository import Repository, vector_literal
from noteflow_worker.memory.models import (
    CONVERSATION_STATUSES,
    MEMORY_STATUS_ACTIVE,
    MEMORY_STATUS_SUPERSEDED,
    ConversationInfo,
    ConversationMessage,
    ConversationState,
    ConversationSummary,
    MemoryRecord,
    SourceScope,
)

# Advisory-lock namespace for conversation maintenance. Any int32 works as
# long as it does not collide with other advisory-lock users in this database.
MAINTENANCE_LOCK_NAMESPACE = 730_115


class MemoryStore(Repository):
    """SQL persistence for conversation memory.

    Ownership: this store owns rag_messages, rag_conversation_summaries,
    rag_memories, and the memory-state columns on rag_conversations. The
    conversation API owns the remaining rag_conversations columns; both sides
    use idempotent DDL so either service can start first.
    """

    def ensure_memory_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_conversations (
                  id UUID PRIMARY KEY,
                  user_id UUID NOT NULL,
                  title VARCHAR(300),
                  status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
                  active_summary TEXT,
                  active_summary_json TEXT,
                  summary_version INTEGER NOT NULL DEFAULT 0,
                  summary_token_count INTEGER NOT NULL DEFAULT 0,
                  summary_covers_through_at TIMESTAMPTZ,
                  summary_covers_through_message_id UUID,
                  extraction_covers_through_at TIMESTAMPTZ,
                  extraction_covers_through_message_id UUID,
                  last_message_at TIMESTAMPTZ,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for statement in [
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS active_summary_json TEXT",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS summary_version INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS summary_token_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS summary_covers_through_at TIMESTAMPTZ",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS summary_covers_through_message_id UUID",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS extraction_covers_through_at TIMESTAMPTZ",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS extraction_covers_through_message_id UUID",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS selected_pdf_document_ids JSONB NOT NULL DEFAULT '[]'",
                "ALTER TABLE rag_conversations ADD COLUMN IF NOT EXISTS selected_ai_note_document_ids JSONB NOT NULL DEFAULT '[]'",
            ]:
                conn.execute(statement)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rag_conversations_user_recent
                ON rag_conversations(user_id, last_message_at DESC NULLS LAST)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_user_preferences (
                  user_id UUID NOT NULL,
                  preference_key VARCHAR(64) NOT NULL,
                  preference_value VARCHAR(400) NOT NULL,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  PRIMARY KEY (user_id, preference_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_messages (
                  id UUID PRIMARY KEY,
                  conversation_id UUID NOT NULL,
                  role VARCHAR(32) NOT NULL,
                  status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED',
                  content_markdown TEXT,
                  token_count INTEGER NOT NULL DEFAULT 0,
                  metadata_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rag_messages_conversation_created
                ON rag_messages(conversation_id, created_at, id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_conversation_summaries (
                  id UUID PRIMARY KEY,
                  conversation_id UUID NOT NULL,
                  version INTEGER NOT NULL,
                  summary_text TEXT NOT NULL,
                  summary_json TEXT,
                  token_count INTEGER NOT NULL DEFAULT 0,
                  covered_message_count INTEGER NOT NULL DEFAULT 0,
                  covers_through_at TIMESTAMPTZ,
                  covers_through_message_id UUID,
                  provider VARCHAR(64),
                  model VARCHAR(128),
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(conversation_id, version)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_memories (
                  id UUID PRIMARY KEY,
                  user_id UUID NOT NULL,
                  conversation_id UUID,
                  memory_type VARCHAR(32) NOT NULL,
                  content TEXT NOT NULL,
                  content_hash VARCHAR(128) NOT NULL,
                  confidence DOUBLE PRECISION NOT NULL,
                  status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
                  source_message_id UUID,
                  superseded_by UUID,
                  embedding vector,
                  embedding_provider VARCHAR(64),
                  embedding_model VARCHAR(128),
                  embedding_dimension INTEGER,
                  access_count INTEGER NOT NULL DEFAULT 0,
                  last_accessed_at TIMESTAMPTZ,
                  expires_at TIMESTAMPTZ,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rag_memories_user_status
                ON rag_memories(user_id, status)
                """
            )

    # ------------------------------------------------------------------
    # Conversation state and messages
    # ------------------------------------------------------------------

    def load_conversation_state(self, conversation_id: str) -> ConversationState:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, status, active_summary, active_summary_json, summary_version,
                       summary_token_count, summary_covers_through_at, summary_covers_through_message_id,
                       extraction_covers_through_at, extraction_covers_through_message_id,
                       selected_pdf_document_ids, selected_ai_note_document_ids
                FROM rag_conversations
                WHERE id = %s
                """,
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        return ConversationState(
            conversation_id=str(row["id"]),
            user_id=str(row["user_id"]),
            status=row["status"],
            active_summary=row["active_summary"],
            active_summary_json=row["active_summary_json"],
            summary_version=row["summary_version"],
            summary_token_count=row["summary_token_count"],
            summary_covers_through_at=row["summary_covers_through_at"],
            summary_covers_through_message_id=(
                str(row["summary_covers_through_message_id"]) if row["summary_covers_through_message_id"] else None
            ),
            extraction_covers_through_at=row["extraction_covers_through_at"],
            extraction_covers_through_message_id=(
                str(row["extraction_covers_through_message_id"]) if row["extraction_covers_through_message_id"] else None
            ),
            source_scope=SourceScope(
                pdf_document_ids=parse_id_list(row["selected_pdf_document_ids"]),
                ai_note_document_ids=parse_id_list(row["selected_ai_note_document_ids"]),
            ),
        )

    def ensure_conversation(self, conversation_id: str, user_id: str, title: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_conversations (id, user_id, title)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (conversation_id, user_id, title),
            )

    def create_conversation(self, user_id: str, title: str | None = None) -> str:
        conversation_id = str(uuid4())
        self.ensure_conversation(conversation_id, user_id, title)
        return conversation_id

    def list_conversations(
        self,
        user_id: str,
        limit: int,
        include_archived: bool = False,
    ) -> list[ConversationInfo]:
        statuses = ("ACTIVE", "ARCHIVED") if include_archived else ("ACTIVE",)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, title, status, last_message_at, created_at
                FROM rag_conversations
                WHERE user_id = %s AND status = ANY(%s)
                ORDER BY last_message_at DESC NULLS LAST, created_at DESC
                LIMIT %s
                """,
                (user_id, list(statuses), limit),
            ).fetchall()
        return [
            ConversationInfo(
                conversation_id=str(row["id"]),
                user_id=str(row["user_id"]),
                title=row["title"],
                status=row["status"],
                last_message_at=ensure_utc(row["last_message_at"]),
                created_at=ensure_utc(row["created_at"]),
            )
            for row in rows
        ]

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> bool:
        with self.connect() as conn:
            result = conn.execute(
                "UPDATE rag_conversations SET title = %s, updated_at = NOW() WHERE id = %s AND user_id = %s",
                (title.strip()[:300], conversation_id, user_id),
            )
            return result.rowcount == 1

    def set_conversation_status(self, conversation_id: str, user_id: str, status: str) -> bool:
        if status not in CONVERSATION_STATUSES:
            raise ValueError(f"Unsupported conversation status: {status}")
        with self.connect() as conn:
            result = conn.execute(
                "UPDATE rag_conversations SET status = %s, updated_at = NOW() WHERE id = %s AND user_id = %s",
                (status, conversation_id, user_id),
            )
            return result.rowcount == 1

    def set_conversation_sources(self, conversation_id: str, user_id: str, scope: SourceScope) -> bool:
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE rag_conversations
                SET selected_pdf_document_ids = %s::jsonb,
                    selected_ai_note_document_ids = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                """,
                (
                    json.dumps(sorted(set(scope.pdf_document_ids)), separators=(",", ":")),
                    json.dumps(sorted(set(scope.ai_note_document_ids)), separators=(",", ":")),
                    conversation_id,
                    user_id,
                ),
            )
            return result.rowcount == 1

    def missing_document_ids(self, user_id: str, document_ids: list[str]) -> list[str]:
        """Return the subset of ids that do not exist or are not owned by the user."""
        unique_ids = sorted(set(document_ids))
        if not unique_ids:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM documents WHERE id = ANY(%s::uuid[]) AND user_id = %s",
                (unique_ids, user_id),
            ).fetchall()
        owned = {str(row["id"]) for row in rows}
        return [item for item in unique_ids if item not in owned]

    # ------------------------------------------------------------------
    # User preferences (global, explicit settings)
    # ------------------------------------------------------------------

    def load_user_preferences(self, user_id: str) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT preference_key, preference_value FROM rag_user_preferences WHERE user_id = %s",
                (user_id,),
            ).fetchall()
        return {row["preference_key"]: row["preference_value"] for row in rows}

    def upsert_user_preference(self, user_id: str, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_user_preferences (user_id, preference_key, preference_value)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, preference_key)
                DO UPDATE SET preference_value = EXCLUDED.preference_value, updated_at = NOW()
                """,
                (user_id, key, value),
            )

    def delete_user_preference(self, user_id: str, key: str) -> bool:
        with self.connect() as conn:
            result = conn.execute(
                "DELETE FROM rag_user_preferences WHERE user_id = %s AND preference_key = %s",
                (user_id, key),
            )
            return result.rowcount == 1

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        token_count: int,
        metadata_json: str | None = None,
    ) -> str:
        message_id = str(uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_messages (id, conversation_id, role, content_markdown, token_count, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (message_id, conversation_id, role, content, token_count, metadata_json),
            )
            conn.execute(
                "UPDATE rag_conversations SET last_message_at = NOW(), updated_at = NOW() WHERE id = %s",
                (conversation_id,),
            )
        return message_id

    def load_messages_after(
        self,
        conversation_id: str,
        after_at: datetime | None,
        after_message_id: str | None,
        limit: int,
    ) -> list[ConversationMessage]:
        """Load unconsumed messages strictly after the (created_at, id) watermark."""
        with self.connect() as conn:
            if after_at is not None and after_message_id is not None:
                rows = conn.execute(
                    """
                    SELECT id, conversation_id, role, status, content_markdown, token_count, metadata_json, created_at
                    FROM rag_messages
                    WHERE conversation_id = %s AND (created_at, id) > (%s, %s)
                    ORDER BY created_at, id
                    LIMIT %s
                    """,
                    (conversation_id, after_at, after_message_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, conversation_id, role, status, content_markdown, token_count, metadata_json, created_at
                    FROM rag_messages
                    WHERE conversation_id = %s
                    ORDER BY created_at, id
                    LIMIT %s
                    """,
                    (conversation_id, limit),
                ).fetchall()
        return [message_from_row(row) for row in rows]

    def unsummarized_token_count(
        self,
        conversation_id: str,
        after_at: datetime | None,
        after_message_id: str | None,
    ) -> int:
        with self.connect() as conn:
            if after_at is not None and after_message_id is not None:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(token_count), 0) AS total
                    FROM rag_messages
                    WHERE conversation_id = %s AND (created_at, id) > (%s, %s)
                    """,
                    (conversation_id, after_at, after_message_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(token_count), 0) AS total FROM rag_messages WHERE conversation_id = %s",
                    (conversation_id,),
                ).fetchone()
        return int(row["total"])

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def save_summary(self, summary: ConversationSummary, expected_version: int) -> bool:
        """Persist a new summary version with optimistic concurrency.

        Returns False when another maintenance run advanced the version first;
        the caller treats that as a benign lost race, not an error.
        """
        with self.connect() as conn:
            updated = conn.execute(
                """
                UPDATE rag_conversations
                SET active_summary = %s,
                    active_summary_json = %s,
                    summary_version = %s,
                    summary_token_count = %s,
                    summary_covers_through_at = %s,
                    summary_covers_through_message_id = %s,
                    updated_at = NOW()
                WHERE id = %s AND summary_version = %s
                """,
                (
                    summary.summary_text,
                    summary.summary_json,
                    summary.version,
                    summary.token_count,
                    summary.covers_through_at,
                    summary.covers_through_message_id,
                    summary.conversation_id,
                    expected_version,
                ),
            )
            if updated.rowcount != 1:
                return False
            conn.execute(
                """
                INSERT INTO rag_conversation_summaries (
                  id, conversation_id, version, summary_text, summary_json, token_count,
                  covered_message_count, covers_through_at, covers_through_message_id, provider, model
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (conversation_id, version) DO NOTHING
                """,
                (
                    str(uuid4()),
                    summary.conversation_id,
                    summary.version,
                    summary.summary_text,
                    summary.summary_json,
                    summary.token_count,
                    summary.covered_message_count,
                    summary.covers_through_at,
                    summary.covers_through_message_id,
                    summary.provider,
                    summary.model,
                ),
            )
        return True

    def advance_extraction_watermark(
        self,
        conversation_id: str,
        covers_through_at: datetime,
        covers_through_message_id: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE rag_conversations
                SET extraction_covers_through_at = %s,
                    extraction_covers_through_message_id = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND (extraction_covers_through_at IS NULL OR extraction_covers_through_at <= %s)
                """,
                (covers_through_at, covers_through_message_id, conversation_id, covers_through_at),
            )

    # ------------------------------------------------------------------
    # Long-term memories
    # ------------------------------------------------------------------

    def insert_memory(self, record: MemoryRecord) -> str:
        memory_id = record.id or str(uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO rag_memories (
                  id, user_id, conversation_id, memory_type, content, content_hash, confidence,
                  status, source_message_id, embedding, embedding_provider, embedding_model,
                  embedding_dimension, expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s)
                """,
                (
                    memory_id,
                    record.user_id,
                    record.conversation_id,
                    record.memory_type,
                    record.content,
                    record.content_hash,
                    record.confidence,
                    record.status,
                    record.source_message_id,
                    vector_literal(record.embedding) if record.embedding else None,
                    record.embedding_provider,
                    record.embedding_model,
                    len(record.embedding) if record.embedding else None,
                    record.expires_at,
                ),
            )
        return memory_id

    def supersede_memory(self, old_memory_id: str, new_memory_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE rag_memories
                SET status = %s, superseded_by = %s, updated_at = NOW()
                WHERE id = %s AND status = %s
                """,
                (MEMORY_STATUS_SUPERSEDED, new_memory_id, old_memory_id, MEMORY_STATUS_ACTIVE),
            )

    def load_active_memories(self, user_id: str, limit: int) -> list[MemoryRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, conversation_id, memory_type, content, content_hash, confidence,
                       status, source_message_id, superseded_by, embedding::text AS embedding_text,
                       embedding_provider, embedding_model, access_count, last_accessed_at,
                       expires_at, created_at, updated_at
                FROM rag_memories
                WHERE user_id = %s AND status = %s AND (expires_at IS NULL OR expires_at > NOW())
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (user_id, MEMORY_STATUS_ACTIVE, limit),
            ).fetchall()
        return [memory_from_row(row) for row in rows]

    def search_memories_by_embedding(
        self,
        user_id: str,
        query_embedding: list[float],
        embedding_provider: str,
        embedding_model: str,
        candidate_limit: int,
    ) -> list[tuple[MemoryRecord, float]]:
        """Vector recall over the user's active memories.

        Per-user active memories are bounded (memory_max_active_per_user), so
        an exact scan ordered by pgvector cosine distance is fast without an
        ANN index; matching on provider/model prevents cross-space distances.
        """
        literal = vector_literal(query_embedding)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, conversation_id, memory_type, content, content_hash, confidence,
                       status, source_message_id, superseded_by, NULL AS embedding_text,
                       embedding_provider, embedding_model, access_count, last_accessed_at,
                       expires_at, created_at, updated_at,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM rag_memories
                WHERE user_id = %s
                  AND status = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND embedding IS NOT NULL
                  AND embedding_provider = %s
                  AND embedding_model = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    literal,
                    user_id,
                    MEMORY_STATUS_ACTIVE,
                    embedding_provider,
                    embedding_model,
                    literal,
                    candidate_limit,
                ),
            ).fetchall()
        return [(memory_from_row(row), float(row["similarity"])) for row in rows]

    def touch_memory_access(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE rag_memories
                SET access_count = access_count + 1, last_accessed_at = NOW()
                WHERE id = ANY(%s)
                """,
                (memory_ids,),
            )

    def count_active_memories(self, user_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM rag_memories WHERE user_id = %s AND status = %s",
                (user_id, MEMORY_STATUS_ACTIVE),
            ).fetchone()
        return int(row["total"])

    def expire_lowest_value_memories(self, user_id: str, excess: int) -> int:
        """Bound per-user storage by expiring the least valuable active rows."""
        if excess <= 0:
            return 0
        with self.connect() as conn:
            result = conn.execute(
                """
                UPDATE rag_memories
                SET status = 'EXPIRED', updated_at = NOW()
                WHERE id IN (
                  SELECT id FROM rag_memories
                  WHERE user_id = %s AND status = %s
                  ORDER BY confidence ASC, COALESCE(last_accessed_at, created_at) ASC
                  LIMIT %s
                )
                """,
                (user_id, MEMORY_STATUS_ACTIVE, excess),
            )
            return result.rowcount

    # ------------------------------------------------------------------
    # Maintenance coordination
    # ------------------------------------------------------------------

    @contextmanager
    def maintenance_lock(self, conversation_id: str):
        """Session-scoped advisory lock so one conversation is maintained by
        at most one worker at a time; concurrent attempts yield False and skip."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT pg_try_advisory_lock(%s, hashtext(%s)) AS locked",
                (MAINTENANCE_LOCK_NAMESPACE, conversation_id),
            ).fetchone()
            locked = bool(row["locked"])
            try:
                yield locked
            finally:
                if locked:
                    conn.execute(
                        "SELECT pg_advisory_unlock(%s, hashtext(%s))",
                        (MAINTENANCE_LOCK_NAMESPACE, conversation_id),
                    )


def message_from_row(row: dict) -> ConversationMessage:
    return ConversationMessage(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        role=row["role"],
        content=row["content_markdown"] or "",
        token_count=row["token_count"] or 0,
        created_at=ensure_utc(row["created_at"]),
        status=row["status"],
        metadata_json=row["metadata_json"],
    )


def memory_from_row(row: dict) -> MemoryRecord:
    return MemoryRecord(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        conversation_id=str(row["conversation_id"]) if row["conversation_id"] else None,
        memory_type=row["memory_type"],
        content=row["content"],
        content_hash=row["content_hash"],
        confidence=float(row["confidence"]),
        status=row["status"],
        source_message_id=str(row["source_message_id"]) if row["source_message_id"] else None,
        embedding=parse_vector_text(row.get("embedding_text")),
        embedding_provider=row["embedding_provider"],
        embedding_model=row["embedding_model"],
        created_at=ensure_utc(row["created_at"]),
        updated_at=ensure_utc(row["updated_at"]),
        expires_at=ensure_utc(row["expires_at"]),
        access_count=row["access_count"] or 0,
        last_accessed_at=ensure_utc(row["last_accessed_at"]),
        superseded_by=str(row["superseded_by"]) if row["superseded_by"] else None,
    )


def parse_id_list(value) -> list[str]:
    """JSONB round-trips as a Python list under psycopg3; tolerate raw strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def parse_vector_text(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return [float(item) for item in parsed] if isinstance(parsed, list) else None


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def default_expiry(ttl_days: int | None) -> datetime | None:
    if ttl_days is None or ttl_days <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=ttl_days)
