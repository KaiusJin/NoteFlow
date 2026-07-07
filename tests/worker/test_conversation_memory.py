import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from noteflow_worker.memory.consolidation import (
    DECISION_ADD,
    DECISION_SKIP,
    DECISION_UPDATE,
    decide_consolidation,
    memory_content_hash,
)
from noteflow_worker.memory.extraction import extract_memory_candidates, validate_extraction_payload
from noteflow_worker.memory.llm import MemoryLlmError, StructuredMemoryLlm
from noteflow_worker.memory.manager import ConversationMemoryManager
from noteflow_worker.memory.models import (
    ConversationMessage,
    ConversationState,
    MemoryCandidate,
    MemoryRecord,
    SourceScope,
)
from noteflow_worker.memory.preferences import (
    long_term_memory_enabled,
    render_preferences_for_prompt,
    validate_preference,
)
from noteflow_worker.memory.recall import rank_recalled_memories
from noteflow_worker.memory.summarizer import validate_summary_payload
from noteflow_worker.memory.window import select_window, should_compress, split_for_compression
from noteflow_worker.queue.redis_queue import PRIORITY_BACKGROUND, RedisTaskQueue, priority_for_task_type


BASE_TIME = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def message(index: int, content: str, role: str = "USER", token_count: int | None = None) -> ConversationMessage:
    return ConversationMessage(
        id=f"00000000-0000-0000-0000-{index:012d}",
        conversation_id="conv-1",
        role=role,
        content=content,
        token_count=token_count if token_count is not None else max(1, len(content.split())),
        created_at=BASE_TIME + timedelta(minutes=index),
    )


def memory_record(
    record_id: str,
    content: str,
    *,
    memory_type: str = "KNOWN_DIFFICULTY",
    confidence: float = 0.8,
    embedding: list[float] | None = None,
    age_days: float = 0.0,
) -> MemoryRecord:
    moment = BASE_TIME - timedelta(days=age_days)
    return MemoryRecord(
        id=record_id,
        user_id="user-1",
        conversation_id="conv-1",
        memory_type=memory_type,
        content=content,
        content_hash=memory_content_hash(content),
        confidence=confidence,
        status="ACTIVE",
        source_message_id=None,
        embedding=embedding,
        embedding_provider="gemini" if embedding else None,
        embedding_model="gemini-embedding-001" if embedding else None,
        created_at=moment,
        updated_at=moment,
    )


class SlidingWindowTest(unittest.TestCase):
    def test_window_prefers_newest_messages_within_token_budget(self):
        messages = [message(index, f"turn {index} " + "word " * 40, token_count=50) for index in range(10)]
        selection = select_window(messages, max_tokens=160, min_turns=1, max_turns=12, message_max_tokens=900)
        self.assertEqual([item.id for item in selection.window], [messages[7].id, messages[8].id, messages[9].id])
        self.assertLessEqual(selection.token_count, 160)
        self.assertEqual(selection.excluded_message_count, 7)

    def test_min_turns_overrides_token_budget(self):
        messages = [message(index, "long " * 200, token_count=250) for index in range(4)]
        selection = select_window(messages, max_tokens=100, min_turns=2, max_turns=12, message_max_tokens=900)
        self.assertEqual(len(selection.window), 2)

    def test_overlong_message_is_clipped_not_dropped(self):
        messages = [message(0, "huge " * 3000, token_count=3900), message(1, "short reply", token_count=3)]
        selection = select_window(messages, max_tokens=500, min_turns=2, max_turns=12, message_max_tokens=200)
        self.assertEqual(len(selection.window), 2)
        self.assertIn(messages[0].id, selection.clipped_message_ids)
        clipped = next(item for item in selection.window if item.id == messages[0].id)
        self.assertIn("truncated for context window", clipped.content)
        self.assertLessEqual(clipped.token_count, 260)

    def test_compression_trigger_and_low_water_split(self):
        self.assertFalse(should_compress(3200, 3200))
        self.assertTrue(should_compress(3201, 3200))
        messages = [message(index, "content", token_count=100) for index in range(20)]
        evicted, retained = split_for_compression(messages, retain_tokens=400)
        self.assertEqual(len(retained), 4)
        self.assertEqual(len(evicted), 16)
        self.assertEqual(retained[-1].id, messages[-1].id)
        self.assertEqual(evicted[0].id, messages[0].id)


class SummaryValidationTest(unittest.TestCase):
    def _payload(self, important_ids: list[str]) -> dict:
        return {
            "topicsCovered": ["geometric distribution"],
            "userGoals": ["prepare for midterm"],
            "establishedDefinitions": ["PMF of geometric"],
            "unresolvedQuestions": [],
            "sourceDocumentsDiscussed": ["STAT230 June 17"],
            "importantMessageIds": important_ids,
            "narrative": "The student reviewed the geometric distribution.",
        }

    def test_invented_message_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_summary_payload(self._payload(["fake-id"]), known_message_ids={"real-id"})

    def test_valid_summary_passes(self):
        validate_summary_payload(self._payload(["real-id"]), known_message_ids={"real-id"})


class ExtractionTest(unittest.TestCase):
    def test_disallowed_type_and_invented_source_are_rejected(self):
        base = {
            "memoryType": "KNOWN_DIFFICULTY",
            "content": "Struggles with geometric distributions.",
            "confidence": 0.9,
            "sourceMessageId": "m1",
            "ttlDays": 0,
        }
        with self.assertRaises(ValueError):
            validate_extraction_payload({"memories": [{**base, "memoryType": "HEALTH_STATUS"}]}, {"m1"})
        with self.assertRaises(ValueError):
            validate_extraction_payload({"memories": [{**base, "sourceMessageId": "invented"}]}, {"m1"})
        validate_extraction_payload({"memories": [base]}, {"m1"})

    def test_low_confidence_candidates_are_filtered_and_ttl_mapped(self):
        response_payload = {
            "memories": [
                {
                    "memoryType": "KNOWN_DIFFICULTY",
                    "content": "Struggles with geometric distributions.",
                    "confidence": 0.9,
                    "sourceMessageId": "00000000-0000-0000-0000-000000000001",
                    "ttlDays": 0,
                },
                {
                    "memoryType": "LEARNING_GOAL",
                    "content": "Might be interested in combinatorics.",
                    "confidence": 0.2,
                    "sourceMessageId": "00000000-0000-0000-0000-000000000001",
                    "ttlDays": 0,
                },
                {
                    "memoryType": "LEARNING_GOAL",
                    "content": "Preparing for the STAT 230 midterm on July 20.",
                    "confidence": 1.0,
                    "sourceMessageId": "00000000-0000-0000-0000-000000000001",
                    "ttlDays": 30,
                },
            ]
        }

        def fake_request(prompt, schema, name):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(response_payload)}]}}]}

        llm = StructuredMemoryLlm("gemini", "test-model", request_fn=fake_request)
        candidates = extract_memory_candidates(llm, [message(1, "I keep failing geometric problems")], None)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].memory_type, "KNOWN_DIFFICULTY")
        self.assertIsNone(candidates[0].ttl_days)
        self.assertEqual(candidates[1].ttl_days, 30)


class LlmRetryTest(unittest.TestCase):
    def test_validation_failures_are_retried_then_succeed(self):
        calls = {"count": 0}
        good = {"memories": []}

        def fake_request(prompt, schema, name):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(good)}]}}]}

        llm = StructuredMemoryLlm("gemini", "test-model", request_fn=fake_request)
        with patch("noteflow_worker.memory.llm.settings.memory_retry_backoff_seconds", 0.0):
            parsed = llm.generate("prompt", {"type": "OBJECT"}, "schema", lambda value: None)
        self.assertEqual(parsed, good)
        self.assertEqual(calls["count"], 2)

    def test_non_retryable_error_fails_fast(self):
        calls = {"count": 0}

        def fake_request(prompt, schema, name):
            calls["count"] += 1
            raise RuntimeError("Memory LLM HTTP 401: unauthorized")

        llm = StructuredMemoryLlm("gemini", "test-model", request_fn=fake_request)
        with self.assertRaises(MemoryLlmError):
            llm.generate("prompt", {"type": "OBJECT"}, "schema", lambda value: None)
        self.assertEqual(calls["count"], 1)


class ConsolidationTest(unittest.TestCase):
    def _candidate(self, content: str, confidence: float = 0.9) -> MemoryCandidate:
        return MemoryCandidate(
            memory_type="KNOWN_DIFFICULTY",
            content=content,
            confidence=confidence,
            source_message_id=None,
        )

    def test_exact_duplicate_is_skipped(self):
        existing = [memory_record("m1", "Struggles with geometric distributions.")]
        decision = decide_consolidation(
            self._candidate("struggles  with geometric distributions."),
            None,
            existing,
            dedup_threshold=0.90,
            update_threshold=0.78,
        )
        self.assertEqual(decision.action, DECISION_SKIP)
        self.assertEqual(decision.reason, "exact_duplicate")

    def test_semantic_duplicate_updates_only_with_higher_confidence(self):
        existing = [memory_record("m1", "Has difficulty with geometric distribution.", confidence=0.6, embedding=[1.0, 0.0])]
        near_duplicate = self._candidate("Finds geometric distribution problems hard.", confidence=0.95)
        decision = decide_consolidation(near_duplicate, [0.999, 0.02], existing, dedup_threshold=0.90, update_threshold=0.78)
        self.assertEqual(decision.action, DECISION_UPDATE)
        lower_confidence = self._candidate("Finds geometric distribution problems hard.", confidence=0.5)
        decision = decide_consolidation(lower_confidence, [0.999, 0.02], existing, dedup_threshold=0.90, update_threshold=0.78)
        self.assertEqual(decision.action, DECISION_SKIP)

    def test_mid_similarity_refines_and_low_similarity_adds(self):
        existing = [memory_record("m1", "Struggles with geometric distribution.", embedding=[1.0, 0.0])]
        refine = decide_consolidation(self._candidate("new detail"), [0.85, 0.53], existing, dedup_threshold=0.95, update_threshold=0.80)
        self.assertEqual(refine.action, DECISION_UPDATE)
        add = decide_consolidation(self._candidate("unrelated"), [0.0, 1.0], existing, dedup_threshold=0.95, update_threshold=0.80)
        self.assertEqual(add.action, DECISION_ADD)

    def test_records_from_other_embedding_space_only_exact_dedupe(self):
        existing = [memory_record("m1", "Struggles with geometric distribution.", embedding=None)]
        decision = decide_consolidation(self._candidate("Different memory content."), [1.0, 0.0], existing, dedup_threshold=0.90, update_threshold=0.78)
        self.assertEqual(decision.action, DECISION_ADD)


class RecallRankingTest(unittest.TestCase):
    def test_similarity_floor_weights_and_token_budget(self):
        fresh = memory_record("fresh", "Struggles with geometric distributions.", confidence=0.9)
        stale = memory_record("stale", "Preferred worked examples last term.", confidence=0.9, age_days=90.0)
        weak = memory_record("weak", "Might like combinatorics.", confidence=0.9)
        with patch("noteflow_worker.memory.recall.settings.memory_recall_min_similarity", 0.55), patch(
            "noteflow_worker.memory.recall.settings.memory_recall_limit", 5
        ), patch("noteflow_worker.memory.recall.settings.memory_recall_max_tokens", 600):
            ranked = rank_recalled_memories(
                [(fresh, 0.80), (stale, 0.80), (weak, 0.40)],
                now=BASE_TIME,
            )
        self.assertEqual([item.record.id for item in ranked], ["fresh", "stale"])
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_count_limit_is_enforced(self):
        records = [
            (memory_record(f"m{index}", f"memory {index}", confidence=0.9), 0.9 - index * 0.01)
            for index in range(10)
        ]
        with patch("noteflow_worker.memory.recall.settings.memory_recall_limit", 3):
            ranked = rank_recalled_memories(records, now=BASE_TIME)
        self.assertEqual(len(ranked), 3)


class FakeStore:
    def __init__(self, state: ConversationState, messages: list[ConversationMessage], vector_hits, active, preferences=None):
        self.state = state
        self.messages = messages
        self.vector_hits = vector_hits
        self.active = active
        self.preferences = preferences or {}
        self.touched: list[list[str]] = []

    def load_conversation_state(self, conversation_id):
        return self.state

    def load_messages_after(self, conversation_id, after_at, after_message_id, limit):
        return self.messages[:limit]

    def search_memories_by_embedding(self, user_id, embedding, provider, model, limit):
        return self.vector_hits

    def load_active_memories(self, user_id, limit):
        return self.active[:limit]

    def touch_memory_access(self, memory_ids):
        self.touched.append(memory_ids)

    def load_user_preferences(self, user_id):
        return dict(self.preferences)


class FakeEmbeddingProvider:
    provider_name = "gemini"
    model = "gemini-embedding-001"

    def embed_texts(self, texts):
        from noteflow_worker.embeddings.providers import EmbeddingResult

        return [EmbeddingResult([1.0, 0.0]) for _ in texts]


class ManagerReadPathTest(unittest.TestCase):
    def _state(self) -> ConversationState:
        return ConversationState(
            conversation_id="conv-1",
            user_id="user-1",
            active_summary="Narrative: reviewed PMFs.",
            active_summary_json="{}",
            summary_version=3,
            summary_token_count=40,
            summary_covers_through_at=BASE_TIME,
            summary_covers_through_message_id="00000000-0000-0000-0000-000000000001",
            extraction_covers_through_at=None,
            extraction_covers_through_message_id=None,
        )

    def test_context_combines_summary_window_and_vector_recall(self):
        hit = memory_record("m1", "Struggles with geometric distributions.", embedding=[1.0, 0.0])
        store = FakeStore(self._state(), [message(2, "What is a CDF?"), message(3, "How does it differ?")], [(hit, 0.9)], [])
        manager = ConversationMemoryManager(store=store, embedding_provider=FakeEmbeddingProvider())
        context = manager.build_context("conv-1", "user-1", "difference between PMF and CDF")
        self.assertEqual(context.summary_text, "Narrative: reviewed PMFs.")
        self.assertEqual(len(context.window), 2)
        self.assertEqual(context.recalled_memories[0].record.id, "m1")
        self.assertEqual(context.diagnostics["recallMode"], "vector")
        self.assertEqual(store.touched, [["m1"]])
        self.assertGreater(context.total_token_count, 0)

    def test_vector_empty_falls_back_to_recent_high_confidence(self):
        fallback = memory_record("m2", "Prefers worked examples.")
        store = FakeStore(self._state(), [message(2, "hello")], [], [fallback])
        manager = ConversationMemoryManager(store=store, embedding_provider=FakeEmbeddingProvider())
        context = manager.build_context("conv-1", "user-1", "unrelated question")
        self.assertEqual(context.diagnostics["recallMode"], "vector_empty_fallback")
        self.assertEqual(context.recalled_memories[0].record.id, "m2")


class PreferencesAndScopeTest(unittest.TestCase):
    def test_preference_validation_whitelist_and_enums(self):
        self.assertEqual(validate_preference("EXPLANATION_DEPTH", "detailed"), "DETAILED")
        self.assertEqual(validate_preference("ANSWER_LANGUAGE", "中文"), "中文")
        with self.assertRaises(ValueError):
            validate_preference("FAVORITE_COLOR", "blue")
        with self.assertRaises(ValueError):
            validate_preference("EXPLANATION_DEPTH", "extreme")
        with self.assertRaises(ValueError):
            validate_preference("ANSWER_STYLE", "x" * 400)

    def test_long_term_memory_switch_and_prompt_rendering(self):
        self.assertTrue(long_term_memory_enabled({}))
        self.assertFalse(long_term_memory_enabled({"LONG_TERM_MEMORY": "DISABLED"}))
        rendered = render_preferences_for_prompt({"ANSWER_LANGUAGE": "中文", "LONG_TERM_MEMORY": "DISABLED"})
        self.assertIn("ANSWER_LANGUAGE: 中文", rendered)
        self.assertNotIn("LONG_TERM_MEMORY", rendered)

    def test_context_carries_preferences_and_source_scope(self):
        state = ConversationState(
            conversation_id="conv-1",
            user_id="user-1",
            active_summary=None,
            active_summary_json=None,
            summary_version=0,
            summary_token_count=0,
            summary_covers_through_at=None,
            summary_covers_through_message_id=None,
            extraction_covers_through_at=None,
            extraction_covers_through_message_id=None,
            source_scope=SourceScope(pdf_document_ids=["doc-1"], ai_note_document_ids=["doc-2"]),
        )
        store = FakeStore(state, [message(1, "hi")], [], [], preferences={"ANSWER_LANGUAGE": "中文"})
        manager = ConversationMemoryManager(store=store, embedding_provider=FakeEmbeddingProvider())
        context = manager.build_context("conv-1", "user-1", "question")
        self.assertEqual(context.preferences, {"ANSWER_LANGUAGE": "中文"})
        self.assertEqual(context.source_scope.pdf_document_ids, ["doc-1"])
        self.assertFalse(context.source_scope.is_unrestricted)

    def test_memory_opt_out_disables_recall(self):
        state = ConversationState(
            conversation_id="conv-1",
            user_id="user-1",
            active_summary=None,
            active_summary_json=None,
            summary_version=0,
            summary_token_count=0,
            summary_covers_through_at=None,
            summary_covers_through_message_id=None,
            extraction_covers_through_at=None,
            extraction_covers_through_message_id=None,
        )
        hit = memory_record("m1", "Struggles with geometric distributions.", embedding=[1.0, 0.0])
        store = FakeStore(state, [message(1, "hi")], [(hit, 0.9)], [], preferences={"LONG_TERM_MEMORY": "DISABLED"})
        manager = ConversationMemoryManager(store=store, embedding_provider=FakeEmbeddingProvider())
        context = manager.build_context("conv-1", "user-1", "question")
        self.assertEqual(context.recalled_memories, [])
        self.assertEqual(context.diagnostics["recallMode"], "disabled_by_preference")

    def test_foreign_user_is_rejected(self):
        state = ConversationState(
            conversation_id="conv-1",
            user_id="owner-user",
            active_summary=None,
            active_summary_json=None,
            summary_version=0,
            summary_token_count=0,
            summary_covers_through_at=None,
            summary_covers_through_message_id=None,
            extraction_covers_through_at=None,
            extraction_covers_through_message_id=None,
        )
        store = FakeStore(state, [], [], [])
        manager = ConversationMemoryManager(store=store, embedding_provider=FakeEmbeddingProvider())
        with self.assertRaises(PermissionError):
            manager.build_context("conv-1", "attacker", "question")

    def test_scope_with_unowned_documents_is_rejected(self):
        class ScopeStore(FakeStore):
            def missing_document_ids(self, user_id, document_ids):
                return [item for item in document_ids if item == "foreign-doc"]

            def set_conversation_sources(self, conversation_id, user_id, scope):
                return True

        store = ScopeStore(None, [], [], [])
        manager = ConversationMemoryManager(store=store, embedding_provider=FakeEmbeddingProvider())
        with self.assertRaises(ValueError):
            manager.set_conversation_sources("conv-1", "user-1", SourceScope(pdf_document_ids=["foreign-doc"]))
        self.assertTrue(
            manager.set_conversation_sources("conv-1", "user-1", SourceScope(pdf_document_ids=["owned-doc"]))
        )


class QueuePayloadTest(unittest.TestCase):
    def test_memory_maintenance_is_background_and_conversation_id_round_trips(self):
        self.assertEqual(priority_for_task_type("MAINTAIN_CONVERSATION_MEMORY"), PRIORITY_BACKGROUND)
        queue = RedisTaskQueue.__new__(RedisTaskQueue)
        raw = json.dumps(
            {
                "taskId": "t1",
                "documentId": "",
                "userId": "u1",
                "taskType": "MAINTAIN_CONVERSATION_MEMORY",
                "priority": PRIORITY_BACKGROUND,
                "enqueuedAt": 1.0,
                "conversationId": "conv-9",
            }
        )
        decoded = queue._decode(raw, PRIORITY_BACKGROUND)
        self.assertEqual(decoded.conversation_id, "conv-9")
        legacy = queue._decode(
            json.dumps({"taskId": "t2", "documentId": "d1", "userId": "u1", "taskType": "PARSE_DOCUMENT"}),
            PRIORITY_BACKGROUND,
        )
        self.assertIsNone(legacy.conversation_id)


if __name__ == "__main__":
    unittest.main()
