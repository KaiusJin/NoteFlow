import json
import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from datetime import datetime, timezone

from noteflow_worker.db.repository import TextChunk
from noteflow_worker.config import settings
from noteflow_worker.study.common import allocate_item_targets, build_source_groups, dedupe_hash, is_near_duplicate, resolve_sources
from noteflow_worker.study.models import ReviewState
from noteflow_worker.study.providers import (
    StructuredStudyProvider, flashcards_from_dict, flashcards_response_schema, grade_response_schema, questions_from_dict,
    questions_response_schema, request_with_retries, validate_grade_response,
)
from noteflow_worker.study.srs import reset_review, resume_review, schedule_review, suspend_review
from noteflow_worker.pipelines.generate_flashcards import generate_group as generate_flashcard_group
from noteflow_worker.pipelines.generate_quiz import difficulty_counts, generate_group as generate_quiz_group, parse_difficulty_mix


def valid_card(**overrides):
    value = {"cardType": "DEFINITION", "front": "What is variance?", "back": "$E[X^2]-E[X]^2$",
             "clozeText": "", "difficulty": "EASY", "topic": "Variance", "hint": "Second moment",
             "tags": ["probability"], "sourceChunkIndexes": [0], "confidence": 0.9, "warnings": []}
    value.update(overrides)
    return value


def valid_question(**overrides):
    value = {"questionType": "MULTIPLE_CHOICE", "difficulty": "MEDIUM", "topic": "Variance",
             "stem": "Which is Var(X)?", "options": ["A", "B", "C"], "correctAnswer": "B",
             "answerKey": "B, because ...", "rubric": [{"point": "Select B", "weight": 2.0}],
             "explanation": "Variance is the second central moment.", "relatedFormula": "$Var(X)$",
             "commonMistake": "Using the raw second moment.", "distractorRationales": ["A is mean.", "C is SD."],
             "points": 2.0, "sourceChunkIndexes": [0], "confidence": 0.95, "warnings": []}
    value.update(overrides)
    return value


class StructuredStudyOutputTest(unittest.TestCase):
    def test_accepts_flashcard_and_preserves_latex(self):
        cards = flashcards_from_dict({"flashcards": [valid_card()]})
        self.assertEqual(cards[0].back, "$E[X^2]-E[X]^2$")
        self.assertEqual(cards[0].tags, ["probability"])

    def test_cloze_requires_cloze_text(self):
        with self.assertRaises(ValueError):
            flashcards_from_dict({"flashcards": [valid_card(cardType="CLOZE")]})

    def test_rejects_unknown_flashcard_field(self):
        card = valid_card()
        card["invented"] = True
        with self.assertRaises(ValueError):
            flashcards_from_dict({"flashcards": [card]})

    def test_accepts_valid_mcq_and_rubric(self):
        questions = questions_from_dict({"questions": [valid_question()]})
        self.assertEqual(questions[0].points, 2.0)

    def test_rejects_rubric_total_mismatch(self):
        with self.assertRaises(ValueError):
            questions_from_dict({"questions": [valid_question(points=3.0)]})

    def test_rejects_missing_distractor_rationale(self):
        with self.assertRaises(ValueError):
            questions_from_dict({"questions": [valid_question(distractorRationales=["only one"])]})

    def test_grade_requires_one_boolean_per_rubric_item(self):
        validate_grade_response({"isCorrect": False, "awardedPoints": 1, "feedback": "One point hit.",
                                 "keyPointsHit": [True, False]}, 2, 2)
        with self.assertRaises(ValueError):
            validate_grade_response({"isCorrect": True, "awardedPoints": 3, "feedback": "Too many.",
                                     "keyPointsHit": [True]}, 2, 1)

    def test_openai_conversion_can_make_all_study_schemas_strict(self):
        from noteflow_worker.notes.providers import convert_gemini_schema_to_json_schema
        for schema in (flashcards_response_schema(), questions_response_schema(), grade_response_schema()):
            converted = convert_gemini_schema_to_json_schema(schema)
            self.assertFalse(converted["additionalProperties"])

    def test_difficulty_allocation_is_normalized_and_exact(self):
        self.assertAlmostEqual(sum(parse_difficulty_mix("EASY:3,MEDIUM:5,HARD:2").values()), 1.0)
        counts = difficulty_counts(7, "EASY:0.3,MEDIUM:0.5,HARD:0.2")
        self.assertEqual(sum(counts.values()), 7)
        self.assertGreaterEqual(counts["MEDIUM"], counts["EASY"])

    def test_group_cardinality_mismatch_is_retried(self):
        class Provider:
            calls = 0
            def generate_flashcards(self, _prompt):
                self.calls += 1
                return [object()] * (1 if self.calls == 1 else 2)
        provider = Provider()
        self.assertEqual(len(generate_flashcard_group(provider, "prompt", 2)), 2)
        self.assertEqual(provider.calls, 2)

    def test_group_difficulty_mismatch_is_retried(self):
        class Candidate:
            def __init__(self, difficulty): self.difficulty = difficulty
        class Provider:
            calls = 0
            def generate_questions(self, _prompt):
                self.calls += 1
                return [Candidate("HARD")] if self.calls == 1 else [Candidate("EASY")]
        provider = Provider()
        self.assertEqual(generate_quiz_group(provider, "prompt", {"EASY": 1, "MEDIUM": 0, "HARD": 0})[0].difficulty,
                         "EASY")
        self.assertEqual(provider.calls, 2)

    def test_provider_usage_aggregates_gemini_and_openai_metadata(self):
        provider = StructuredStudyProvider("openai", "model", "key")
        provider._record_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}})
        provider._record_usage({"usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2,
                                                   "totalTokenCount": 7}})
        self.assertEqual(provider.usage_snapshot(), {"inputTokens": 15, "outputTokens": 6,
                                                     "totalTokens": 21, "successfulResponses": 2})

    def test_provider_repr_never_exposes_api_key(self):
        provider = StructuredStudyProvider("openai", "model", "super-secret-key")
        self.assertNotIn("super-secret-key", repr(provider))

    def test_process_wide_provider_semaphore_bounds_concurrency(self):
        active = maximum = 0
        lock = threading.Lock()

        class Response:
            def __enter__(self):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.01)
                return self
            def __exit__(self, *_args):
                nonlocal active
                with lock: active -= 1
            def read(self):
                return b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

        with patch("noteflow_worker.study.providers.urllib.request.urlopen", return_value=Response()):
            with ThreadPoolExecutor(max_workers=24) as executor:
                results = list(executor.map(lambda _: request_with_retries("https://example.invalid", {}, {}), range(24)))
        self.assertTrue(all(result == {"ok": True} for result in results))
        self.assertLessEqual(maximum, settings.study_global_max_concurrent_requests)
        self.assertGreater(maximum, 1)


class SourceGroupingAndGroundingTest(unittest.TestCase):
    def setUp(self):
        self.chunks = [TextChunk(page_number=i + 1, chunk_index=i, content="word " * 80, token_count=80,
                                 id=f"00000000-0000-0000-0000-{i:012d}") for i in range(5)]

    def test_groups_preserve_order_and_every_chunk(self):
        groups = build_source_groups(self.chunks, target_tokens=150, max_tokens=200)
        flattened = [chunk.chunk_index for group in groups for chunk in group.chunks]
        self.assertEqual(flattened, list(range(5)))
        self.assertEqual([group.index for group in groups], list(range(len(groups))))

    def test_oversized_chunk_is_not_dropped(self):
        groups = build_source_groups([TextChunk(1, 0, "x", token_count=999, id="id")], 100, 200)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].chunks[0].id, "id")

    def test_resolves_only_valid_group_citations(self):
        group = build_source_groups(self.chunks, 1000, 1000)[0]
        ids, pages = resolve_sources(group, [0, 2, 2])
        self.assertEqual(len(ids), 2)
        self.assertEqual(pages, [1, 3])
        with self.assertRaises(ValueError):
            resolve_sources(group, [99])

    def test_dedupe_is_case_and_whitespace_insensitive(self):
        self.assertEqual(dedupe_hash("Hello  World"), dedupe_hash("hello world"))
        self.assertTrue(is_near_duplicate("What is variance?", ["what is  variance?"], 0.95))

    def test_item_targets_respect_budget_without_dropping_source_groups(self):
        groups = build_source_groups(self.chunks, 80, 80)
        targets = allocate_item_targets(groups, per_1000_tokens=100, configured_max=7)
        self.assertEqual(sum(targets.values()), 7)
        self.assertTrue(all(count >= 1 for count in targets.values()))
        expanded = allocate_item_targets(groups, per_1000_tokens=1, configured_max=2)
        self.assertEqual(sum(expanded.values()), len(groups))


class Sm2Test(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 6, tzinfo=timezone.utc)
        self.state = ReviewState("user", "card", "NEW", 2.5, 0, 0, self.now, None, None)

    def test_good_reviews_advance_learning_to_review(self):
        first = schedule_review(self.state, "GOOD", self.now)
        second = schedule_review(first, "GOOD", first.due_at)
        self.assertEqual(first.status, "LEARNING")
        self.assertEqual(first.interval_days, 1)
        self.assertEqual(second.status, "REVIEW")
        self.assertEqual(second.interval_days, 6)

    def test_again_resets_repetitions_and_never_below_minimum_ease(self):
        state = ReviewState("user", "card", "REVIEW", 1.3, 30, 8, self.now, self.now, "GOOD")
        result = schedule_review(state, "AGAIN", self.now)
        self.assertEqual(result.repetitions, 0)
        self.assertEqual(result.interval_days, 1)
        self.assertGreaterEqual(result.ease_factor, 1.3)

    def test_suspended_card_cannot_be_reviewed(self):
        suspended = ReviewState("user", "card", "SUSPENDED", 2.5, 0, 0, None, None, None)
        with self.assertRaises(ValueError):
            schedule_review(suspended, "GOOD", self.now)

    def test_reset_returns_new_due_card(self):
        reviewed = schedule_review(self.state, "EASY", self.now)
        reset = reset_review(reviewed, self.now)
        self.assertEqual((reset.status, reset.repetitions, reset.due_at), ("NEW", 0, self.now))

    def test_suspend_and_resume_restore_derived_phase(self):
        reviewed = schedule_review(schedule_review(self.state, "GOOD", self.now), "GOOD", self.now)
        self.assertEqual(resume_review(suspend_review(reviewed), self.now).status, "REVIEW")


if __name__ == "__main__":
    unittest.main()
