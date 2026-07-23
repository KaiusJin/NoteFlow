from __future__ import annotations

import json
from uuid import uuid4

from noteflow_worker.config import settings
from noteflow_worker.conversation.agent import ToolCallingAgent, agent_state_snapshot, agent_structured_response_json
from noteflow_worker.conversation.answering import make_answer_llm
from noteflow_worker.conversation.retrieval import Evidence
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
            saved_run = self.store.load_agent_snapshot(message_id) if payload.task_type == "RESUME_AGENT_RUN" else None
            question = str(saved_run["question"]) if saved_run else self.load_question(placeholder)

            provider = self.embedding_provider_factory()
            query_embedding = self.embed_query(provider, question)

            context = self.manager.build_context(
                payload.conversation_id, payload.user_id, question, query_embedding=query_embedding
            )
            self.store.mark_task_processing(payload.task_id, "ANSWERING", 40)

            llm = self.llm_factory()
            agent = ToolCallingAgent(self.store, self.queue(), llm, provider)
            state = agent.run(
                payload.conversation_id,
                payload.user_id,
                question,
                context,
                progress_callback=lambda step, progress: self.store.mark_task_processing(payload.task_id, step, progress),
                checkpoint_callback=lambda agent_state: self.store.checkpoint_agent_run(
                    message_id, agent_structured_response_json(agent_state), agent_state.scratchpad
                ),
                snapshot=saved_run.get("state_json") if saved_run else None,
            )
            if state.paused:
                if not state.waiting_task_id:
                    raise RuntimeError("Paused Agent run has no waiting task id.")
                self.store.pause_agent_run(
                    message_id, payload.conversation_id, payload.user_id, question,
                    json.dumps(agent_state_snapshot(state), separators=(",", ":")), state.waiting_task_id,
                )
                # Close the subscribe-after-completion race: this is a no-op
                # while the artifact task is active, but immediately queues a
                # continuation if it already reached a terminal state.
                for row in self.store.create_resume_tasks(state.waiting_task_id):
                    self.queue().push(TaskPayload(
                        task_id=row["task_id"], document_id="", user_id=row["user_id"],
                        task_type="RESUME_AGENT_RUN", conversation_id=row["conversation_id"],
                        message_id=row["message_id"],
                    ))
                self.store.mark_task_completed(payload.task_id)
                return
            answer = state.final
            if answer is None:
                raise RuntimeError("Agent did not produce a final answer.")
            evidence: list[Evidence] = state.evidence
            self.store.mark_task_processing(payload.task_id, "ANSWERING", 70)

            cited = [evidence[index] for index in answer.cited_evidence_indexes]
            self.store.complete_assistant_message(
                message_id,
                answer.answer_markdown,
                estimate_tokens(answer.answer_markdown),
                llm.provider,
                llm.model,
                agent_structured_response_json(state),
                [citation_from_evidence(position, item) for position, item in enumerate(cited)],
            )
            self.store.complete_agent_run(message_id)

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
