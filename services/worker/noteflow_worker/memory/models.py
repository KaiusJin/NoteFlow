from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


MESSAGE_ROLES = {"USER", "ASSISTANT", "TOOL", "SYSTEM_SUMMARY"}

# Long-term memory types accepted by extraction. The whitelist is a privacy
# guardrail: inferred sensitive traits are rejected regardless of what the
# extraction model returns.
MEMORY_TYPES = {
    "USER_PREFERENCE",
    "LEARNING_GOAL",
    "KNOWN_DIFFICULTY",
    "COURSE_CONTEXT",
    "EXPLICIT_FACT",
}

MEMORY_STATUS_ACTIVE = "ACTIVE"
MEMORY_STATUS_SUPERSEDED = "SUPERSEDED"
MEMORY_STATUS_EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    conversation_id: str
    role: str
    content: str
    token_count: int
    created_at: datetime
    status: str = "COMPLETED"
    metadata_json: str | None = None


CONVERSATION_STATUSES = {"ACTIVE", "ARCHIVED", "DELETED"}


@dataclass(frozen=True)
class SourceScope:
    """User-selected retrieval sources for one conversation.

    Empty lists mean unrestricted: the retrieval layer may use every READY
    document the user owns. A non-empty list restricts the corresponding
    source domain (PDF chunks / AI note sections) to those document ids.
    """

    pdf_document_ids: list[str] = field(default_factory=list)
    ai_note_document_ids: list[str] = field(default_factory=list)

    @property
    def is_unrestricted(self) -> bool:
        return not self.pdf_document_ids and not self.ai_note_document_ids


@dataclass(frozen=True)
class ConversationInfo:
    """Conversation list item for the multi-chat sidebar."""

    conversation_id: str
    user_id: str
    title: str | None
    status: str
    last_message_at: datetime | None
    created_at: datetime | None


@dataclass(frozen=True)
class ConversationState:
    """Denormalized per-conversation memory state read in one query."""

    conversation_id: str
    user_id: str
    active_summary: str | None
    active_summary_json: str | None
    summary_version: int
    summary_token_count: int
    summary_covers_through_at: datetime | None
    summary_covers_through_message_id: str | None
    extraction_covers_through_at: datetime | None
    extraction_covers_through_message_id: str | None
    status: str = "ACTIVE"
    source_scope: SourceScope = field(default_factory=SourceScope)


@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: str
    version: int
    summary_text: str
    summary_json: str
    token_count: int
    covered_message_count: int
    covers_through_at: datetime
    covers_through_message_id: str
    provider: str
    model: str


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    user_id: str
    conversation_id: str | None
    memory_type: str
    content: str
    content_hash: str
    confidence: float
    status: str
    source_message_id: str | None
    embedding: list[float] | None
    embedding_provider: str | None
    embedding_model: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    access_count: int = 0
    last_accessed_at: datetime | None = None
    superseded_by: str | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    """A validated candidate produced by extraction, before consolidation."""

    memory_type: str
    content: str
    confidence: float
    source_message_id: str | None
    ttl_days: int | None = None


@dataclass(frozen=True)
class RecalledMemory:
    record: MemoryRecord
    similarity: float
    score: float


@dataclass(frozen=True)
class WorkingContext:
    """The assembled short-term + long-term context for one turn."""

    conversation_id: str
    summary_text: str | None
    summary_json: str | None
    window: list[ConversationMessage]
    recalled_memories: list[RecalledMemory]
    window_token_count: int
    summary_token_count: int
    memory_token_count: int
    preferences: dict[str, str] = field(default_factory=dict)
    source_scope: SourceScope = field(default_factory=SourceScope)
    diagnostics: dict = field(default_factory=dict)

    @property
    def total_token_count(self) -> int:
        return self.window_token_count + self.summary_token_count + self.memory_token_count


@dataclass(frozen=True)
class MaintenanceReport:
    """Outcome of one background maintenance pass, persisted for observability."""

    conversation_id: str
    summarized: bool
    summary_version: int | None
    evicted_message_count: int
    extraction_ran: bool
    candidates_extracted: int
    memories_added: int
    memories_updated: int
    memories_skipped: int
    errors: list[str] = field(default_factory=list)
