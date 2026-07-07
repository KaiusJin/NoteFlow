from __future__ import annotations

import json
from uuid import uuid4

from noteflow_worker.config import settings
from noteflow_worker.conversation.answering import generate_answer, make_answer_llm, structured_response_json
from noteflow_worker.conversation.retrieval import Evidence, search_evidence
from noteflow_worker.conversation.store import Citation, ConversationStore
from noteflow_worker.embeddings.providers import make_embedding_provider
from noteflow_worker.memory.manager import ConversationMemoryManager
from noteflow_worker.pdf.parser import estimate_tokens
from noteflow_worker.queue.redis_queue import RedisTaskQueue, TaskPayload


class AnswerConversationTurnPipeline:
    """One conversation turn, per the six-step contract in
    AGENT_MEMORY_ARCHITECTURE §4.5: the API persisted the USER message and an
    ASSISTANT placeholder; this pipeline builds memory context, retrieves
    scope-filtered evidence with one shared query embedding, generates a
    cited answer, fills the placeholder, and schedules memory maintenance."""

    def __init__(
        self,
        store: ConversationStore | None = None,
        manager: ConversationMemoryManager | None = None,
        queue: RedisTaskQueue | None = None,
        llm_factory=make_answer_llm,
        embedding_provider_factory=make_embedding_provider,
    ) -> None:
        self.store = store or ConversationStore()
        self.manager = manager or ConversationMemoryManager(store=self.store)
        self._queue = queue
        self.llm_factory = llm_factory
        self.embedding_provider_factory = embedding_provider_factory

    def queue(self) -> RedisTaskQueue:
        if self._queue is None:
            self._queue = RedisTaskQueue()
        return self._queue

    def run(self, payload: TaskPayload) -> None:
        if not payload.conversation_id or not payload.message_id:
            self.store.mark_task_failed(payload.task_id, "ANSWER_CONVERSATION_TURN requires conversationId and messageId.")
            raise ValueError("ANSWER_CONVERSATION_TURN payload is missing conversationId or messageId.")
        message_id = payload.message_id
        try:
            self.store.ensure_conversation_schema()
            self.store.bind_task_target(payload.task_id, payload.conversation_id, message_id)
            self.store.mark_task_processing(payload.task_id, "ANSWERING", 10)

            placeholder = self.store.load_message(message_id)
            if placeholder["status"] != "GENERATING":
                # Duplicate delivery or an already-recovered turn; nothing to do.
                self.store.mark_task_completed(payload.task_id)
                return
            question = self.load_question(placeholder)

            provider = self.embedding_provider_factory()
            query_embedding = self.embed_query(provider, question)

            context = self.manager.build_context(
                payload.conversation_id, payload.user_id, question, query_embedding=query_embedding
            )
            self.store.mark_task_processing(payload.task_id, "ANSWERING", 40)

            evidence: list[Evidence] = []
            if query_embedding:
                evidence = search_evidence(
                    self.store, payload.user_id, query_embedding,
                    provider.provider_name, provider.model, context.source_scope,
                )

            llm = self.llm_factory()
            answer = generate_answer(llm, context, evidence, question)
            self.store.mark_task_processing(payload.task_id, "ANSWERING", 70)

            cited = [evidence[index] for index in answer.cited_evidence_indexes]
            self.store.complete_assistant_message(
                message_id,
                answer.answer_markdown,
                estimate_tokens(answer.answer_markdown),
                llm.provider,
                llm.model,
                structured_response_json(answer, evidence),
                [citation_from_evidence(position, item) for position, item in enumerate(cited)],
            )

            self.schedule_maintenance_if_due(payload)
            self.store.mark_task_completed(payload.task_id)
            print(
                "Conversation turn answered "
                + json.dumps(
                    {
                        "conversationId": payload.conversation_id,
                        "messageId": message_id,
                        "evidenceCount": len(evidence),
                        "citedCount": len(cited),
                        "insufficientEvidence": answer.insufficient_evidence,
                        "recallMode": context.diagnostics.get("recallMode"),
                    },
                    separators=(",", ":"),
                )
            )
        except Exception as exc:
            self.store.fail_assistant_message(message_id, str(exc))
            self.store.mark_task_failed(payload.task_id, str(exc))
            raise

    def load_question(self, placeholder: dict) -> str:
        metadata = parse_json_safe(placeholder.get("metadata_json")) or {}
        user_message_id = metadata.get("userMessageId")
        if not user_message_id:
            raise ValueError("Assistant placeholder does not reference its user message.")
        user_message = self.store.load_message(str(user_message_id))
        question = (user_message.get("content_markdown") or "").strip()
        if not question:
            raise ValueError("The user message is empty.")
        return question

    def embed_query(self, provider, question: str) -> list[float] | None:
        if provider.provider_name == "disabled":
            return None
        result = provider.embed_texts([question])[0]
        return None if result.error_message or not result.embedding else result.embedding

    def schedule_maintenance_if_due(self, payload: TaskPayload) -> None:
        try:
            if not self.manager.maintenance_due(payload.conversation_id):
                return
            if settings.memory_maintenance_inline:
                self.manager.run_maintenance(payload.conversation_id)
                return
            maintenance_task_id = str(uuid4())
            self.store.create_maintenance_task(maintenance_task_id, payload.user_id)
            self.queue().push(
                TaskPayload(
                    task_id=maintenance_task_id,
                    document_id="",
                    user_id=payload.user_id,
                    task_type="MAINTAIN_CONVERSATION_MEMORY",
                    conversation_id=payload.conversation_id,
                )
            )
        except Exception as exc:
            # Maintenance scheduling must never fail a successfully answered turn.
            print(f"Memory maintenance scheduling failed (non-fatal): {exc}")


def citation_from_evidence(position: int, item: Evidence) -> Citation:
    return Citation(
        citation_index=position,
        source_domain=item.source_domain,
        source_object_type=item.source_object_type,
        source_object_ids=[item.source_object_id],
        document_id=item.document_id,
        page_start=item.page_start,
        page_end=item.page_end,
        source_title=item.title or item.document_title,
        evidence_snapshot=item.snippet or item.text[:600],
        retrieval_score=item.similarity,
    )


def parse_json_safe(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
