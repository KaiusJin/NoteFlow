from __future__ import annotations

import json
from dataclasses import dataclass

from noteflow_worker.config import settings
from noteflow_worker.embeddings.providers import EmbeddingProvider, make_embedding_provider
from noteflow_worker.memory.consolidation import (
    DECISION_ADD,
    DECISION_SKIP,
    DECISION_UPDATE,
    decide_consolidation,
    memory_content_hash,
)
from noteflow_worker.memory.extraction import extract_memory_candidates
from noteflow_worker.memory.llm import StructuredMemoryLlm, make_memory_llm
from noteflow_worker.memory.models import (
    MEMORY_STATUS_ACTIVE,
    MESSAGE_ROLES,
    ConversationMessage,
    ConversationState,
    ConversationSummary,
    MaintenanceReport,
    MemoryRecord,
    RecalledMemory,
    SourceScope,
    WorkingContext,
)
from noteflow_worker.memory.preferences import long_term_memory_enabled, validate_preference
from noteflow_worker.memory.recall import rank_recalled_memories, recalled_token_count
from noteflow_worker.memory.store import MemoryStore, default_expiry
from noteflow_worker.memory.summarizer import build_rolling_summary, summary_text, summary_token_count
from noteflow_worker.memory.window import select_window, should_compress, split_for_compression
from noteflow_worker.pdf.parser import estimate_tokens


@dataclass(frozen=True)
class TurnRecord:
    message_id: str
    token_count: int
    maintenance_needed: bool


def require_conversation_owner(state: ConversationState, user_id: str) -> None:
    """Defense in depth: the API layer owns authorization, but a mismatched
    user id here means a caller bug that would leak another user's context."""
    if state.user_id != user_id:
        raise PermissionError(
            f"Conversation {state.conversation_id} does not belong to the requesting user."
        )


class ConversationMemoryManager:
    """Read/write facade for conversation short-term and long-term memory.

    The hot read path (`build_context`) performs a bounded number of queries
    and no LLM calls. All LLM work (summary compression, memory extraction and
    consolidation) happens in `run_maintenance`, which is designed to run as a
    background task; `record_turn` only decides whether maintenance is due.
    """

    def __init__(
        self,
        store: MemoryStore | None = None,
        llm: StructuredMemoryLlm | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._store = store or MemoryStore()
        self._llm = llm
        self._embedding_provider = embedding_provider

    def llm(self) -> StructuredMemoryLlm:
        if self._llm is None:
            self._llm = make_memory_llm()
        return self._llm

    def embedding_provider(self) -> EmbeddingProvider:
        if self._embedding_provider is None:
            self._embedding_provider = make_embedding_provider()
        return self._embedding_provider

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def build_context(
        self,
        conversation_id: str,
        user_id: str,
        current_query: str,
        query_embedding: list[float] | None = None,
    ) -> WorkingContext:
        state = self._store.load_conversation_state(conversation_id)
        require_conversation_owner(state, user_id)
        preferences = self._store.load_user_preferences(user_id)
        messages = self._store.load_messages_after(
            conversation_id,
            state.summary_covers_through_at,
            state.summary_covers_through_message_id,
            settings.memory_window_fetch_limit,
        )
        selection = select_window(
            messages,
            max_tokens=settings.memory_window_max_tokens,
            min_turns=settings.memory_window_min_turns,
            max_turns=settings.memory_window_max_turns,
            message_max_tokens=settings.memory_window_message_max_tokens,
        )
        recalled: list[RecalledMemory] = []
        recall_diagnostics: dict = {"recallMode": "disabled_by_preference"}
        if long_term_memory_enabled(preferences):
            recalled, recall_diagnostics = self._recall_memories(user_id, current_query, query_embedding)
        return WorkingContext(
            conversation_id=conversation_id,
            summary_text=state.active_summary,
            summary_json=state.active_summary_json,
            window=selection.window,
            recalled_memories=recalled,
            window_token_count=selection.token_count,
            summary_token_count=state.summary_token_count,
            memory_token_count=recalled_token_count(recalled),
            preferences=preferences,
            source_scope=state.source_scope,
            diagnostics={
                "summaryVersion": state.summary_version,
                "windowClippedMessageIds": selection.clipped_message_ids,
                "windowExcludedMessageCount": selection.excluded_message_count,
                **recall_diagnostics,
            },
        )

    def _recall_memories(
        self,
        user_id: str,
        current_query: str,
        query_embedding: list[float] | None,
    ) -> tuple[list[RecalledMemory], dict]:
        diagnostics: dict = {"recallMode": "vector"}
        provider = self.embedding_provider()
        embedding = query_embedding
        if embedding is None and provider.provider_name != "disabled" and current_query.strip():
            result = provider.embed_texts([current_query])[0]
            if result.error_message:
                diagnostics["recallEmbeddingError"] = result.error_message[:500]
            else:
                embedding = result.embedding

        recalled: list[RecalledMemory] = []
        if embedding:
            candidates = self._store.search_memories_by_embedding(
                user_id,
                embedding,
                provider.provider_name,
                provider.model,
                settings.memory_recall_candidate_limit,
            )
            recalled = rank_recalled_memories(candidates)

        if not recalled:
            # Degraded mode: embedding provider disabled/failed, the embedding
            # space changed, or nothing cleared the similarity floor. Recent
            # high-confidence memories are still safer than none.
            diagnostics["recallMode"] = "recency_fallback" if embedding is None else "vector_empty_fallback"
            records = self._store.load_active_memories(user_id, settings.memory_recall_fallback_limit)
            recalled = [
                RecalledMemory(record=record, similarity=0.0, score=record.confidence)
                for record in records
            ]

        try:
            self._store.touch_memory_access([item.record.id for item in recalled])
        except Exception as exc:  # access stats are advisory, never break reads
            diagnostics["accessStatsError"] = str(exc)[:200]
        diagnostics["recalledCount"] = len(recalled)
        return recalled, diagnostics

    # ------------------------------------------------------------------
    # Conversation, source-scope, and preference management
    # ------------------------------------------------------------------

    def create_conversation(self, user_id: str, title: str | None = None) -> str:
        return self._store.create_conversation(user_id, title)

    def list_conversations(self, user_id: str, limit: int, include_archived: bool = False):
        return self._store.list_conversations(user_id, limit, include_archived)

    def rename_conversation(self, conversation_id: str, user_id: str, title: str) -> bool:
        if not title.strip():
            raise ValueError("Conversation title must be a non-empty string.")
        return self._store.rename_conversation(conversation_id, user_id, title)

    def set_conversation_status(self, conversation_id: str, user_id: str, status: str) -> bool:
        return self._store.set_conversation_status(conversation_id, user_id, status)

    def set_conversation_sources(self, conversation_id: str, user_id: str, scope: SourceScope) -> bool:
        """Set the retrieval source scope after verifying document ownership.

        Rejecting unknown/foreign ids here keeps a stale UI or a crafted
        request from silently widening retrieval beyond the user's documents.
        """
        missing = self._store.missing_document_ids(
            user_id,
            [*scope.pdf_document_ids, *scope.ai_note_document_ids],
        )
        if missing:
            raise ValueError(f"Documents not found or not owned by user: {missing[:10]}")
        return self._store.set_conversation_sources(conversation_id, user_id, scope)

    def get_user_preferences(self, user_id: str) -> dict[str, str]:
        return self._store.load_user_preferences(user_id)

    def set_user_preference(self, user_id: str, key: str, value: str) -> str:
        normalized = validate_preference(key, value)
        self._store.upsert_user_preference(user_id, key, normalized)
        return normalized

    def clear_user_preference(self, user_id: str, key: str) -> bool:
        return self._store.delete_user_preference(user_id, key)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record_turn(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        metadata_json: str | None = None,
    ) -> TurnRecord:
        if role not in MESSAGE_ROLES:
            raise ValueError(f"Unsupported message role: {role}")
        self._store.ensure_conversation(conversation_id, user_id)
        state = self._store.load_conversation_state(conversation_id)
        require_conversation_owner(state, user_id)
        token_count = estimate_tokens(content)
        message_id = self._store.append_message(conversation_id, role, content, token_count, metadata_json)
        maintenance_needed = self._maintenance_due(state)
        if maintenance_needed and settings.memory_maintenance_inline:
            self.run_maintenance(conversation_id)
            maintenance_needed = False
        return TurnRecord(message_id=message_id, token_count=token_count, maintenance_needed=maintenance_needed)

    def maintenance_due(self, conversation_id: str) -> bool:
        """Public trigger check for callers that persist messages themselves."""
        return self._maintenance_due(self._store.load_conversation_state(conversation_id))

    def _maintenance_due(self, state: ConversationState) -> bool:
        conversation_id = state.conversation_id
        summary_backlog = self._store.unsummarized_token_count(
            conversation_id,
            state.summary_covers_through_at,
            state.summary_covers_through_message_id,
        )
        if should_compress(summary_backlog, settings.memory_summary_trigger_tokens):
            return True
        extraction_backlog = self._store.unsummarized_token_count(
            conversation_id,
            state.extraction_covers_through_at,
            state.extraction_covers_through_message_id,
        )
        return extraction_backlog >= settings.memory_extraction_min_new_tokens

    # ------------------------------------------------------------------
    # Maintenance path (background)
    # ------------------------------------------------------------------

    def run_maintenance(self, conversation_id: str) -> MaintenanceReport:
        with self._store.maintenance_lock(conversation_id) as locked:
            if not locked:
                return MaintenanceReport(
                    conversation_id=conversation_id,
                    summarized=False,
                    summary_version=None,
                    evicted_message_count=0,
                    extraction_ran=False,
                    candidates_extracted=0,
                    memories_added=0,
                    memories_updated=0,
                    memories_skipped=0,
                    errors=["maintenance_already_running"],
                )
            return self._run_maintenance_locked(conversation_id)

    def _run_maintenance_locked(self, conversation_id: str) -> MaintenanceReport:
        state = self._store.load_conversation_state(conversation_id)
        errors: list[str] = []

        summarized, summary_version, evicted_count = False, None, 0
        try:
            summarized, summary_version, evicted_count = self._compress_if_needed(state)
        except Exception as exc:
            errors.append(f"summary: {str(exc)[:500]}")

        extraction_ran = False
        candidates_extracted = added = updated = skipped = 0
        try:
            extraction_ran, candidates_extracted, added, updated, skipped = self._extract_and_consolidate(state)
        except Exception as exc:
            errors.append(f"extraction: {str(exc)[:500]}")

        return MaintenanceReport(
            conversation_id=conversation_id,
            summarized=summarized,
            summary_version=summary_version,
            evicted_message_count=evicted_count,
            extraction_ran=extraction_ran,
            candidates_extracted=candidates_extracted,
            memories_added=added,
            memories_updated=updated,
            memories_skipped=skipped,
            errors=errors,
        )

    def _compress_if_needed(self, state: ConversationState) -> tuple[bool, int | None, int]:
        backlog_tokens = self._store.unsummarized_token_count(
            state.conversation_id,
            state.summary_covers_through_at,
            state.summary_covers_through_message_id,
        )
        if not should_compress(backlog_tokens, settings.memory_summary_trigger_tokens):
            return False, None, 0
        messages = self._store.load_messages_after(
            state.conversation_id,
            state.summary_covers_through_at,
            state.summary_covers_through_message_id,
            settings.memory_maintenance_fetch_limit,
        )
        evicted, _retained = split_for_compression(messages, retain_tokens=settings.memory_summary_retain_tokens)
        if not evicted:
            return False, None, 0
        llm = self.llm()
        summary = build_rolling_summary(llm, state.active_summary_json, evicted)
        text = summary_text(summary)
        last = evicted[-1]
        saved = self._store.save_summary(
            ConversationSummary(
                conversation_id=state.conversation_id,
                version=state.summary_version + 1,
                summary_text=text,
                summary_json=json.dumps(summary, separators=(",", ":")),
                token_count=summary_token_count(text),
                covered_message_count=len(evicted),
                covers_through_at=last.created_at,
                covers_through_message_id=last.id,
                provider=llm.provider,
                model=llm.model,
            ),
            expected_version=state.summary_version,
        )
        if not saved:
            # Another maintenance run advanced the summary first; benign race.
            return False, None, 0
        return True, state.summary_version + 1, len(evicted)

    def _extract_and_consolidate(self, state: ConversationState) -> tuple[bool, int, int, int, int]:
        if not long_term_memory_enabled(self._store.load_user_preferences(state.user_id)):
            # Explicit privacy opt-out: never extract, regardless of backlog.
            return False, 0, 0, 0, 0
        messages = self._store.load_messages_after(
            state.conversation_id,
            state.extraction_covers_through_at,
            state.extraction_covers_through_message_id,
            settings.memory_extraction_max_messages,
        )
        conversational = [message for message in messages if message.role in {"USER", "ASSISTANT"}]
        new_tokens = sum(message.token_count for message in conversational)
        if not conversational or new_tokens < settings.memory_extraction_min_new_tokens:
            return False, 0, 0, 0, 0

        candidates = extract_memory_candidates(self.llm(), conversational, state.active_summary)
        added = updated = skipped = 0
        if candidates:
            added, updated, skipped = self._consolidate(state, candidates)
        last = messages[-1]
        self._store.advance_extraction_watermark(state.conversation_id, last.created_at, last.id)
        return True, len(candidates), added, updated, skipped

    def _consolidate(self, state: ConversationState, candidates) -> tuple[int, int, int]:
        provider = self.embedding_provider()
        embeddings: list[list[float] | None] = [None] * len(candidates)
        if provider.provider_name != "disabled":
            results = provider.embed_texts([candidate.content for candidate in candidates])
            embeddings = [result.embedding if not result.error_message else None for result in results]

        existing = self._store.load_active_memories(state.user_id, settings.memory_max_active_per_user)
        added = updated = skipped = 0
        for candidate, embedding in zip(candidates, embeddings):
            decision = decide_consolidation(
                candidate,
                embedding,
                existing,
                dedup_threshold=settings.memory_dedup_similarity_threshold,
                update_threshold=settings.memory_update_similarity_threshold,
            )
            if decision.action == DECISION_SKIP:
                skipped += 1
                continue
            record = MemoryRecord(
                id="",
                user_id=state.user_id,
                conversation_id=state.conversation_id,
                memory_type=candidate.memory_type,
                content=candidate.content,
                content_hash=memory_content_hash(candidate.content),
                confidence=candidate.confidence,
                status=MEMORY_STATUS_ACTIVE,
                source_message_id=candidate.source_message_id,
                embedding=embedding,
                embedding_provider=provider.provider_name if embedding else None,
                embedding_model=provider.model if embedding else None,
                expires_at=default_expiry(candidate.ttl_days),
            )
            new_id = self._store.insert_memory(record)
            if decision.action == DECISION_UPDATE and decision.existing is not None:
                self._store.supersede_memory(decision.existing.id, new_id)
                existing = [item for item in existing if item.id != decision.existing.id]
                updated += 1
            else:
                added += 1
            existing.append(
                MemoryRecord(**{**record.__dict__, "id": new_id})
            )

        active_count = self._store.count_active_memories(state.user_id)
        excess = active_count - settings.memory_max_active_per_user
        if excess > 0:
            self._store.expire_lowest_value_memories(state.user_id, excess)
        return added, updated, skipped
