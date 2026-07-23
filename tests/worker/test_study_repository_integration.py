"""Opt-in PostgreSQL integration tests: NOTEFLOW_RUN_DB_TESTS=1."""
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from noteflow_worker.study.models import Flashcard, GradeResult, QuizQuestion, ReviewState
from noteflow_worker.study.models import FlashcardCandidate, QuizQuestionCandidate, RubricPoint
from noteflow_worker.study.repository import StudyRepository
from noteflow_worker.learning_memory import LearningMemoryRepository
from noteflow_worker.pipelines.generate_flashcards import GenerateFlashcardsPipeline
from noteflow_worker.pipelines.generate_quiz import GenerateQuizPipeline
from noteflow_worker.pipelines.grade_quiz_attempt import GradeQuizAttemptPipeline
from noteflow_worker.queue.redis_queue import TaskPayload


def ensure_legacy_user(conn, user_id):
    """Keep the suite runnable before or after the local-workspace migration."""
    if conn.execute("SELECT to_regclass('users') present").fetchone()["present"]:
        conn.execute("INSERT INTO users(id,display_name,email,created_at,updated_at) VALUES (%s,'Test','test@local',NOW(),NOW()) ON CONFLICT(id) DO NOTHING", (user_id,))


@unittest.skipUnless(os.getenv("NOTEFLOW_RUN_DB_TESTS") == "1", "requires local PostgreSQL")
class StudyRepositoryIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = StudyRepository()
        cls.repo.ensure_study_schema()
        cls.memory = LearningMemoryRepository()
        cls.memory.ensure_schema()

    def setUp(self):
        self.document_id, self.user_id = str(uuid4()), str(uuid4())
        self.deck_id, self.quiz_set_id, self.attempt_id = str(uuid4()), str(uuid4()), str(uuid4())
        with self.repo.connect() as conn:
            ensure_legacy_user(conn, self.user_id)
            conn.execute("""INSERT INTO documents(id,user_id,title,storage_path,file_size,status,document_type,
              content_source_type) VALUES (%s,%s,'Repository Test','/tmp/none.pdf',1,'READY','COURSE_NOTES','TEXT_PDF')""",
              (self.document_id, self.user_id))
            conn.execute("""INSERT INTO flashcard_decks(id,document_id,user_id,version,title)
              VALUES (%s,%s,%s,1,'Integration deck')""", (self.deck_id, self.document_id, self.user_id))
            conn.execute("""INSERT INTO quiz_sets(id,document_id,user_id,version,title,difficulty_distribution_json)
              VALUES (%s,%s,%s,1,'Integration quiz','{}')""", (self.quiz_set_id, self.document_id, self.user_id))

    def tearDown(self):
        with self.repo.connect() as conn:
            conn.execute("DELETE FROM learning_events WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM topic_learning_memory WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM mistake_memory WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM learning_memory_history WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM learning_artifact_links WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM learning_topic_edges WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM study_generation_checkpoints WHERE set_id IN (%s,%s)",
                         (self.deck_id, self.quiz_set_id))
            conn.execute("DELETE FROM flashcard_decks WHERE id=%s", (self.deck_id,))
            conn.execute("DELETE FROM quiz_sets WHERE id=%s", (self.quiz_set_id,))
            conn.execute("DELETE FROM documents WHERE id=%s", (self.document_id,))
            if conn.execute("SELECT to_regclass('users') present").fetchone()["present"]:
                conn.execute("DELETE FROM users WHERE id=%s", (self.user_id,))

    def test_zero_item_checkpoint_is_resumable(self):
        self.repo.save_checkpoint("FLASHCARDS", self.deck_id, 3, 0)
        self.assertEqual(self.repo.completed_flashcard_groups(self.deck_id), {3})

    def test_shared_task_constraints_accept_study_contract(self):
        task_ids = []
        with self.repo.connect() as conn:
            for task_type, step in (("GENERATE_FLASHCARDS", "GENERATING_FLASHCARDS"),
                                    ("GENERATE_QUIZ", "GENERATING_QUIZ"),
                                    ("GRADE_QUIZ_ATTEMPT", "GRADING_QUIZ")):
                task_id = str(uuid4())
                task_ids.append(task_id)
                conn.execute("""INSERT INTO tasks(id,document_id,user_id,task_type,status,current_step,progress,retry_count)
                  VALUES (%s,%s,%s,%s,'PROCESSING',%s,1,0)""",
                  (task_id, self.document_id, self.user_id, task_type, step))
            conn.execute("DELETE FROM tasks WHERE id=ANY(%s)", (task_ids,))

    def test_generation_and_attempt_ownership_are_enforced(self):
        foreign_user = str(uuid4())
        self.assertEqual(self.repo.latest_generating_deck_id(self.document_id, self.user_id), self.deck_id)
        with self.assertRaises(RuntimeError):
            self.repo.latest_generating_deck_id(self.document_id, foreign_user)
        with self.assertRaises(PermissionError):
            self.repo.assert_document_owner(self.document_id, foreign_user)

    def test_execution_lease_allows_only_one_worker(self):
        key, first, second = f"test:{uuid4()}", str(uuid4()), str(uuid4())
        self.assertTrue(self.repo.acquire_execution_lease(key, first, 60))
        self.assertFalse(self.repo.acquire_execution_lease(key, second, 60))
        self.repo.renew_execution_lease(key, first, 60)
        self.repo.release_execution_lease(key, first)
        self.assertTrue(self.repo.acquire_execution_lease(key, second, 60))
        self.repo.release_execution_lease(key, second)

    def test_document_delete_cascades_study_data_and_checkpoints(self):
        self.repo.save_checkpoint("FLASHCARDS", self.deck_id, 0, 0)
        self.repo.save_checkpoint("QUIZ", self.quiz_set_id, 0, 0)
        with self.repo.connect() as conn:
            conn.execute("DELETE FROM documents WHERE id=%s", (self.document_id,))
            decks = conn.execute("SELECT COUNT(*) count FROM flashcard_decks WHERE document_id=%s",
                                 (self.document_id,)).fetchone()["count"]
            quizzes = conn.execute("SELECT COUNT(*) count FROM quiz_sets WHERE document_id=%s",
                                   (self.document_id,)).fetchone()["count"]
            checkpoints = conn.execute("SELECT COUNT(*) count FROM study_generation_checkpoints WHERE set_id IN (%s,%s)",
                                       (self.deck_id, self.quiz_set_id)).fetchone()["count"]
        self.assertEqual((decks, quizzes, checkpoints), (0, 0, 0))

    def test_card_idempotency_and_distribution(self):
        card = Flashcard(str(uuid4()), self.deck_id, self.document_id, 0, "DEFINITION", "Front", "Back", "",
                         "EASY", "Topic", "Hint", ["tag"], 0, 0, [str(uuid4())], [1], "a" * 64, 0.9, "[]", "{}")
        self.repo.save_flashcard(card)
        self.repo.save_flashcard(card)
        self.assertEqual(self.repo.count_items("flashcards", "deck_id", self.deck_id), 1)
        self.assertEqual(self.repo.item_distribution("flashcards", "deck_id", self.deck_id, "difficulty"), {"EASY": 1})
        self.repo.save_review_state(ReviewState(self.user_id, card.id, "NEW", 2.5, 0, 0,
                                                datetime.now(timezone.utc), None, None))
        self.assertEqual(len(self.repo.load_due_flashcards(self.user_id, self.deck_id)), 1)

    def test_question_grade_checkpoint_and_attempt_totals(self):
        question = QuizQuestion(str(uuid4()), self.quiz_set_id, self.document_id, 0, "SHORT_ANSWER", "MEDIUM",
            "Topic", "Explain it", "[]", "Answer", "Detailed answer", json.dumps([{"point": "Key", "weight": 2.0}]),
            "Explanation", "", "", "[]", 2.0, 0, 0, [str(uuid4())], [2], "b" * 64, 0.9, "[]")
        self.repo.save_quiz_question(question)
        answer_id = str(uuid4())
        with self.repo.connect() as conn:
            conn.execute("INSERT INTO quiz_attempts(id,quiz_set_id,user_id,status) VALUES (%s,%s,%s,'GRADING')",
                         (self.attempt_id, self.quiz_set_id, self.user_id))
            conn.execute("""INSERT INTO quiz_answers(id,attempt_id,question_id,user_response)
              VALUES (%s,%s,%s,'Student answer')""", (answer_id, self.attempt_id, question.id))
        pending = self.repo.load_answers_to_grade(self.attempt_id)
        self.assertEqual(len(pending), 1)
        self.repo.save_grade(answer_id, GradeResult(True, 2.0, "Complete", [True], "LLM"))
        score, maximum, remaining = self.repo.complete_attempt_if_graded(self.attempt_id)
        self.assertEqual((score, maximum, remaining), (2.0, 2.0, 0))

    def test_learning_memory_is_idempotent_under_concurrent_attempt_delivery(self):
        question = QuizQuestion(str(uuid4()), self.quiz_set_id, self.document_id, 0, "SHORT_ANSWER", "HARD",
            "Covariance", "Explain it", "[]", "Answer", "Detailed answer", "[]", "Explanation", "",
            "Confused zero covariance with independence", "[]", 2.0, 0, 0, [], [2], "d" * 64, 0.9, "[]")
        self.repo.save_quiz_question(question)
        answer_id = str(uuid4())
        with self.repo.connect() as conn:
            conn.execute("INSERT INTO quiz_attempts(id,quiz_set_id,user_id,status) VALUES (%s,%s,%s,'COMPLETED')",
                         (self.attempt_id, self.quiz_set_id, self.user_id))
            conn.execute("""INSERT INTO quiz_answers(id,attempt_id,question_id,user_response,is_correct,
              awarded_points,graded_by) VALUES (%s,%s,%s,'Wrong',FALSE,0,'LLM')""",
              (answer_id, self.attempt_id, question.id))
        with ThreadPoolExecutor(max_workers=12) as pool:
            accepted = list(pool.map(lambda _: self.memory.record_quiz_attempt(self.attempt_id, self.user_id), range(48)))
        with self.repo.connect() as conn:
            event_count = conn.execute("SELECT COUNT(*) count FROM learning_events WHERE workspace_id=%s",
                                       (self.user_id,)).fetchone()["count"]
            state = conn.execute("SELECT attempts,incorrect_count,version FROM topic_learning_memory WHERE workspace_id=%s",
                                 (self.user_id,)).fetchone()
            mistake = conn.execute("SELECT occurrences FROM mistake_memory WHERE workspace_id=%s",
                                   (self.user_id,)).fetchone()
        self.assertEqual(sum(accepted), 1)
        self.assertEqual((event_count, state["attempts"], state["incorrect_count"], state["version"],
                          mistake["occurrences"]), (1, 1, 1, 1, 1))


@unittest.skipUnless(os.getenv("NOTEFLOW_RUN_DB_TESTS") == "1", "requires local PostgreSQL")
class StudyPipelineIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = StudyRepository()
        cls.repo.ensure_study_schema()
        cls.memory = LearningMemoryRepository()
        cls.memory.ensure_schema()

    def setUp(self):
        self.user_id, self.document_id, self.chunk_id = str(uuid4()), str(uuid4()), str(uuid4())
        self.created_tasks = []
        with self.repo.connect() as conn:
            ensure_legacy_user(conn, self.user_id)
            conn.execute("""INSERT INTO documents(id,user_id,title,storage_path,file_size,status,document_type,
              content_source_type) VALUES (%s,%s,'Study Pipeline','/tmp/none.pdf',1,'READY','COURSE_NOTES','TEXT_PDF')""",
              (self.document_id, self.user_id))
            conn.execute("""INSERT INTO document_chunks(id,document_id,page_number,chunk_index,content,token_count)
              VALUES (%s,%s,1,0,'Variance is the expected squared deviation from the mean.',100)""",
              (self.chunk_id, self.document_id))

    def tearDown(self):
        with self.repo.connect() as conn:
            conn.execute("DELETE FROM learning_events WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM topic_learning_memory WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM mistake_memory WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM learning_memory_history WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM learning_artifact_links WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM learning_topic_edges WHERE workspace_id=%s", (self.user_id,))
            conn.execute("DELETE FROM tasks WHERE document_id=%s", (self.document_id,))
            conn.execute("""DELETE FROM study_generation_checkpoints WHERE set_id IN
              (SELECT id FROM flashcard_decks WHERE document_id=%s UNION SELECT id FROM quiz_sets WHERE document_id=%s)""",
              (self.document_id, self.document_id))
            conn.execute("DELETE FROM flashcard_decks WHERE document_id=%s", (self.document_id,))
            conn.execute("DELETE FROM quiz_sets WHERE document_id=%s", (self.document_id,))
            conn.execute("DELETE FROM document_chunks WHERE document_id=%s", (self.document_id,))
            conn.execute("DELETE FROM documents WHERE id=%s", (self.document_id,))
            if conn.execute("SELECT to_regclass('users') present").fetchone()["present"]:
                conn.execute("DELETE FROM users WHERE id=%s", (self.user_id,))

    def task(self, task_type, attempt_id=None):
        task_id = str(uuid4())
        with self.repo.connect() as conn:
            conn.execute("""INSERT INTO tasks(id,document_id,user_id,task_type,status,current_step,progress,retry_count)
              VALUES (%s,%s,%s,%s,'PENDING','UPLOADED',0,0)""", (task_id, self.document_id, self.user_id, task_type))
        return TaskPayload(task_id, self.document_id, self.user_id, task_type, attempt_id=attempt_id)

    def test_flashcard_pipeline_reaches_ready_with_grounded_card(self):
        deck_id = str(uuid4())
        with self.repo.connect() as conn:
            conn.execute("""INSERT INTO flashcard_decks(id,document_id,user_id,version,title)
              VALUES (%s,%s,%s,1,'Generated deck')""", (deck_id, self.document_id, self.user_id))

        class Provider:
            provider_name, model = "fake", "fake-v1"
            def generate_flashcards(self, _prompt):
                return [FlashcardCandidate("DEFINITION", "What is variance?", "Expected squared deviation.", "",
                    "EASY", "Variance", "Think of spread.", ["statistics"], [0], 0.99, [])]
            def usage_snapshot(self): return {"inputTokens": 100, "outputTokens": 30, "totalTokens": 130}

        GenerateFlashcardsPipeline(self.repo, lambda: Provider()).run(self.task("GENERATE_FLASHCARDS"))
        with self.repo.connect() as conn:
            deck = conn.execute("SELECT status,completed_source_groups FROM flashcard_decks WHERE id=%s", (deck_id,)).fetchone()
            card = conn.execute("SELECT source_chunk_ids_json FROM flashcards WHERE deck_id=%s", (deck_id,)).fetchone()
            links = conn.execute("SELECT COUNT(*) count FROM learning_artifact_links WHERE artifact_id=%s", (deck_id,)).fetchone()["count"]
        self.assertEqual((deck["status"], deck["completed_source_groups"]), ("READY", 1))
        self.assertEqual(json.loads(card["source_chunk_ids_json"]), [self.chunk_id])
        self.assertEqual(links, 1)

    def test_quiz_pipeline_reaches_ready_with_exact_distribution(self):
        set_id = str(uuid4())
        with self.repo.connect() as conn:
            conn.execute("""INSERT INTO quiz_sets(id,document_id,user_id,version,title,difficulty_distribution_json)
              VALUES (%s,%s,%s,1,'Generated quiz','{}')""", (set_id, self.document_id, self.user_id))

        class Provider:
            provider_name, model = "fake", "fake-v1"
            def generate_questions(self, _prompt):
                return [QuizQuestionCandidate("SHORT_ANSWER", "MEDIUM", "Variance", "Define variance.", [],
                    "Expected squared deviation.", "Variance is E[(X-mu)^2].", [RubricPoint("Definition", 2.0)],
                    "It measures spread.", "$E[(X-\\mu)^2]$", "Confusing variance and SD.", [], 2.0, [0], 0.99, [])]
            def usage_snapshot(self): return {"inputTokens": 100, "outputTokens": 50, "totalTokens": 150}

        GenerateQuizPipeline(self.repo, lambda: Provider()).run(self.task("GENERATE_QUIZ"))
        with self.repo.connect() as conn:
            quiz = conn.execute("SELECT status,completed_source_groups FROM quiz_sets WHERE id=%s", (set_id,)).fetchone()
            count = conn.execute("SELECT COUNT(*) count FROM quiz_questions WHERE quiz_set_id=%s", (set_id,)).fetchone()
            links = conn.execute("SELECT COUNT(*) count FROM learning_artifact_links WHERE artifact_id=%s", (set_id,)).fetchone()["count"]
        self.assertEqual((quiz["status"], quiz["completed_source_groups"], count["count"]), ("READY", 1, 1))
        self.assertEqual(links, 1)

    def test_grading_pipeline_completes_attempt_and_persists_usage(self):
        set_id, question_id, attempt_id, answer_id = (str(uuid4()) for _ in range(4))
        with self.repo.connect() as conn:
            conn.execute("""INSERT INTO quiz_sets(id,document_id,user_id,version,title,difficulty_distribution_json,status)
              VALUES (%s,%s,%s,1,'Grading quiz','{}','READY')""", (set_id, self.document_id, self.user_id))
            conn.execute("""INSERT INTO quiz_questions(id,quiz_set_id,document_id,source_group_index,item_index,
              question_type,difficulty,topic,stem,correct_answer,answer_key,rubric_json,explanation,points,
              source_chunk_ids_json,source_pages_json,dedupe_hash,confidence)
              VALUES (%s,%s,%s,0,0,'SHORT_ANSWER','MEDIUM','Variance','Define variance','Expected squared deviation',
              'Detailed answer','[{"point":"Definition","weight":2.0}]','Explanation',2,'[]','[1]',%s,0.9)""",
              (question_id, set_id, self.document_id, "c" * 64))
            conn.execute("INSERT INTO quiz_attempts(id,quiz_set_id,user_id,status) VALUES (%s,%s,%s,'GRADING')",
                         (attempt_id, set_id, self.user_id))
            conn.execute("INSERT INTO quiz_answers(id,attempt_id,question_id,user_response) VALUES (%s,%s,%s,'A spread measure')",
                         (answer_id, attempt_id, question_id))

        class Provider:
            provider_name, model = "fake", "fake-v1"
            def grade_answer(self, _prompt, _max_points, _rubric_count):
                return GradeResult(True, 2.0, "Complete", [True], "LLM")
            def usage_snapshot(self): return {"inputTokens": 80, "outputTokens": 12, "totalTokens": 92}

        GradeQuizAttemptPipeline(self.repo, lambda: Provider()).run(self.task("GRADE_QUIZ_ATTEMPT", attempt_id))
        with self.repo.connect() as conn:
            attempt = conn.execute("SELECT status,score,max_score,grading_usage_json FROM quiz_attempts WHERE id=%s",
                                   (attempt_id,)).fetchone()
            history = conn.execute("SELECT COUNT(*) count FROM learning_memory_history WHERE workspace_id=%s",
                                   (self.user_id,)).fetchone()["count"]
        self.assertEqual((attempt["status"], attempt["score"], attempt["max_score"]), ("COMPLETED", 2.0, 2.0))
        self.assertEqual(json.loads(attempt["grading_usage_json"])["totalTokens"], 92)
        self.assertEqual(history, 1)


if __name__ == "__main__":
    unittest.main()
