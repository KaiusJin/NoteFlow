import json
import unittest
from unittest.mock import patch

from noteflow_worker.config import settings
from noteflow_worker.conversation.answering import normalize_confidence, validate_answer_payload
from noteflow_worker.conversation.retrieval import Evidence, apply_evidence_budgets
from noteflow_worker.pipelines.answer_conversation_turn import AnswerConversationTurnPipeline
from noteflow_worker.queue.redis_queue import RedisTaskQueue, TaskPayload


def evidence(index: int, similarity: float = 0.9, text: str = "grounded text") -> Evidence:
    return Evidence(
        index=index,
        source_domain="PDF",
        source_object_type="DOCUMENT_CHUNK",
        source_object_id=f"chunk-{index}",
        document_id="00000000-0000-0000-0000-000000000001",
        document_title="Course notes",
        title="Topic",
        page_start=1,
        page_end=1,
        text=text,
        snippet=text,
        similarity=similarity,
    )


class AnswerValidationTest(unittest.TestCase):
    def test_provider_confidence_percent_is_canonicalized(self):
        payload = {"confidence": "80"}
        normalize_confidence(payload)
        self.assertEqual(payload["confidence"], 0.8)

    def test_grounded_answer_requires_valid_citation(self):
        valid = {
            "answerMarkdown": "Supported answer.",
            "citations": [{"evidenceIndex": 0}],
            "confidence": 0.8,
            "insufficientEvidence": False,
        }
        validate_answer_payload(valid, 1)
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_answer_payload({**valid, "citations": [{"evidenceIndex": 2}]}, 1)
        with self.assertRaisesRegex(ValueError, "cite at least one"):
            validate_answer_payload({**valid, "citations": []}, 1)

    def test_insufficient_answer_must_not_claim_citations(self):
        with self.assertRaisesRegex(ValueError, "must not carry citations"):
            validate_answer_payload(
                {
                    "answerMarkdown": "The sources do not establish this.",
                    "citations": [{"evidenceIndex": 0}],
                    "confidence": 0.2,
                    "insufficientEvidence": True,
                },
                1,
            )


class EvidenceBudgetTest(unittest.TestCase):
    def test_similarity_and_count_limits_are_enforced(self):
        candidates = [evidence(0, 0.95), evidence(1, 0.8), evidence(2, 0.2)]
        with patch.object(settings, "answer_evidence_top_k", 1), patch.object(
            settings, "answer_evidence_min_similarity", 0.5
        ):
            selected = apply_evidence_budgets(candidates)
        self.assertEqual([item.source_object_id for item in selected], ["chunk-0"])
        self.assertEqual(selected[0].index, 0)


class AnswerPipelineContractTest(unittest.TestCase):
    def test_placeholder_resolves_exact_user_message(self):
        class Store:
            def load_message(self, message_id):
                self.loaded = message_id
                return {"content_markdown": "  What is variance?  "}

        store = Store()
        pipeline = AnswerConversationTurnPipeline(store=store)
        question = pipeline.load_question({"metadata_json": '{"userMessageId":"user-7"}'})
        self.assertEqual(question, "What is variance?")
        self.assertEqual(store.loaded, "user-7")

    def test_answer_queue_payload_round_trips_message_target(self):
        queue = object.__new__(RedisTaskQueue)
        payload = TaskPayload(
            task_id="task-1",
            document_id="",
            user_id="user-1",
            task_type="ANSWER_CONVERSATION_TURN",
            conversation_id="conversation-1",
            message_id="message-1",
        )
        raw = json.dumps({
            "taskId": payload.task_id,
            "documentId": payload.document_id,
            "userId": payload.user_id,
            "taskType": payload.task_type,
            "conversationId": payload.conversation_id,
            "messageId": payload.message_id,
        })
        decoded = queue._decode(raw, payload.resolved_priority)
        self.assertEqual(decoded.conversation_id, "conversation-1")
        self.assertEqual(decoded.message_id, "message-1")


if __name__ == "__main__":
    unittest.main()
