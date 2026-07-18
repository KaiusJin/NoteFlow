from __future__ import annotations

import json
from uuid import uuid4

from noteflow_worker.db.repository import Repository, ensure_task_constraints
from noteflow_worker.study.models import Flashcard, QuizAnswerToGrade, QuizQuestion, ReviewState


class StudyRepository(Repository):
    """Persistence boundary for generated study material and review state."""

    def ensure_study_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS flashcard_decks (
              id UUID PRIMARY KEY, document_id UUID NOT NULL, user_id UUID NOT NULL, version INTEGER NOT NULL,
              title TEXT NOT NULL, source_scope VARCHAR(32) NOT NULL DEFAULT 'WHOLE_DOCUMENT',
              status VARCHAR(32) NOT NULL DEFAULT 'GENERATING', generation_options_json TEXT NOT NULL DEFAULT '{}',
              provider VARCHAR(64), model VARCHAR(128), prompt_version VARCHAR(64),
              total_source_groups INTEGER NOT NULL DEFAULT 0, completed_source_groups INTEGER NOT NULL DEFAULT 0,
              quality_report_json TEXT, error_message TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(document_id, version))""",
            """CREATE TABLE IF NOT EXISTS flashcards (
              id UUID PRIMARY KEY, deck_id UUID NOT NULL REFERENCES flashcard_decks(id) ON DELETE CASCADE,
              document_id UUID NOT NULL, source_group_index INTEGER NOT NULL, item_index INTEGER NOT NULL,
              card_type VARCHAR(32) NOT NULL, front TEXT NOT NULL, back TEXT NOT NULL, cloze_text TEXT NOT NULL DEFAULT '',
              difficulty VARCHAR(16) NOT NULL, topic TEXT NOT NULL, hint TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',
              source_chunk_ids_json TEXT NOT NULL, source_pages_json TEXT NOT NULL, dedupe_hash VARCHAR(64) NOT NULL,
              confidence DOUBLE PRECISION NOT NULL, warnings_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE(deck_id, source_group_index, item_index), UNIQUE(deck_id, dedupe_hash))""",
            """CREATE TABLE IF NOT EXISTS flashcard_review_states (
              user_id UUID NOT NULL, flashcard_id UUID NOT NULL REFERENCES flashcards(id) ON DELETE CASCADE,
              status VARCHAR(16) NOT NULL DEFAULT 'NEW', ease_factor DOUBLE PRECISION NOT NULL DEFAULT 2.5,
              interval_days INTEGER NOT NULL DEFAULT 0, repetitions INTEGER NOT NULL DEFAULT 0,
              due_at TIMESTAMPTZ, last_reviewed_at TIMESTAMPTZ, last_grade VARCHAR(16),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(user_id, flashcard_id))""",
            """CREATE TABLE IF NOT EXISTS quiz_sets (
              id UUID PRIMARY KEY, document_id UUID NOT NULL, user_id UUID NOT NULL, version INTEGER NOT NULL,
              title TEXT NOT NULL, source_scope VARCHAR(32) NOT NULL DEFAULT 'WHOLE_DOCUMENT',
              status VARCHAR(32) NOT NULL DEFAULT 'GENERATING', difficulty_distribution_json TEXT NOT NULL,
              generation_options_json TEXT NOT NULL DEFAULT '{}', provider VARCHAR(64), model VARCHAR(128), prompt_version VARCHAR(64),
              total_source_groups INTEGER NOT NULL DEFAULT 0, completed_source_groups INTEGER NOT NULL DEFAULT 0,
              quality_report_json TEXT, error_message TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(document_id, version))""",
            """CREATE TABLE IF NOT EXISTS quiz_questions (
              id UUID PRIMARY KEY, quiz_set_id UUID NOT NULL REFERENCES quiz_sets(id) ON DELETE CASCADE,
              document_id UUID NOT NULL, source_group_index INTEGER NOT NULL, item_index INTEGER NOT NULL,
              question_type VARCHAR(32) NOT NULL, difficulty VARCHAR(16) NOT NULL, topic TEXT NOT NULL, stem TEXT NOT NULL,
              options_json TEXT NOT NULL DEFAULT '[]', correct_answer TEXT NOT NULL, answer_key TEXT NOT NULL,
              rubric_json TEXT NOT NULL, explanation TEXT NOT NULL, related_formula TEXT NOT NULL DEFAULT '',
              common_mistake TEXT NOT NULL DEFAULT '', distractor_rationale_json TEXT NOT NULL DEFAULT '[]',
              points DOUBLE PRECISION NOT NULL, source_chunk_ids_json TEXT NOT NULL, source_pages_json TEXT NOT NULL,
              dedupe_hash VARCHAR(64) NOT NULL, confidence DOUBLE PRECISION NOT NULL, warnings_json TEXT NOT NULL DEFAULT '[]',
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE(quiz_set_id, source_group_index, item_index), UNIQUE(quiz_set_id, dedupe_hash))""",
            """CREATE TABLE IF NOT EXISTS quiz_attempts (
              id UUID PRIMARY KEY, quiz_set_id UUID NOT NULL REFERENCES quiz_sets(id) ON DELETE CASCADE, user_id UUID NOT NULL,
              status VARCHAR(32) NOT NULL DEFAULT 'IN_PROGRESS', score DOUBLE PRECISION NOT NULL DEFAULT 0,
              max_score DOUBLE PRECISION NOT NULL DEFAULT 0, weak_topics_json TEXT NOT NULL DEFAULT '[]',
              started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), submitted_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS quiz_answers (
              id UUID PRIMARY KEY, attempt_id UUID NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
              question_id UUID NOT NULL REFERENCES quiz_questions(id) ON DELETE CASCADE, user_response TEXT NOT NULL DEFAULT '',
              is_correct BOOLEAN, awarded_points DOUBLE PRECISION, feedback TEXT, key_points_hit_json TEXT,
              graded_by VARCHAR(16), grading_error TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE(attempt_id, question_id))""",
            """CREATE TABLE IF NOT EXISTS study_generation_checkpoints (
              generation_type VARCHAR(32) NOT NULL, set_id UUID NOT NULL, source_group_index INTEGER NOT NULL,
              status VARCHAR(16) NOT NULL, produced_count INTEGER NOT NULL DEFAULT 0, error_message TEXT,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(generation_type,set_id,source_group_index))""",
            """CREATE TABLE IF NOT EXISTS study_task_targets (
              task_id UUID PRIMARY KEY, attempt_id UUID NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
            """CREATE TABLE IF NOT EXISTS study_execution_leases (
              lease_key TEXT PRIMARY KEY, holder_id UUID NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
            "CREATE INDEX IF NOT EXISTS idx_flashcard_due ON flashcard_review_states(user_id, due_at) WHERE status <> 'SUSPENDED'",
            "CREATE INDEX IF NOT EXISTS idx_flashcards_deck ON flashcards(deck_id, source_group_index, item_index)",
            "CREATE INDEX IF NOT EXISTS idx_quiz_questions_set ON quiz_questions(quiz_set_id, source_group_index, item_index)",
            "CREATE INDEX IF NOT EXISTS idx_quiz_answers_attempt ON quiz_answers(attempt_id, graded_by)",
        ]
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('noteflow-study-schema-v1'))")
            for statement in statements:
                conn.execute(statement)
            conn.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS grading_usage_json TEXT NOT NULL DEFAULT '{}'")
            foreign_keys = (
                ("fk_flashcard_decks_document", "flashcard_decks", "document_id", "documents", "id"),
                ("fk_flashcard_decks_user", "flashcard_decks", "user_id", "users", "id"),
                ("fk_quiz_sets_document", "quiz_sets", "document_id", "documents", "id"),
                ("fk_quiz_sets_user", "quiz_sets", "user_id", "users", "id"),
                ("fk_flashcard_reviews_user", "flashcard_review_states", "user_id", "users", "id"),
                ("fk_quiz_attempts_user", "quiz_attempts", "user_id", "users", "id"),
            )
            for name, table, column, parent, parent_column in foreign_keys:
                exists = conn.execute("SELECT 1 FROM pg_constraint WHERE conname=%s", (name,)).fetchone()
                if not exists:
                    conn.execute(f"""ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({column})
                      REFERENCES {parent}({parent_column}) ON DELETE CASCADE NOT VALID""")
            conn.execute("""CREATE OR REPLACE FUNCTION cleanup_study_generation_checkpoints() RETURNS TRIGGER AS $$
              BEGIN DELETE FROM study_generation_checkpoints WHERE set_id=OLD.id; RETURN OLD; END;
              $$ LANGUAGE plpgsql""")
            for trigger_name, table in (("trg_flashcard_checkpoint_cleanup", "flashcard_decks"),
                                        ("trg_quiz_checkpoint_cleanup", "quiz_sets")):
                exists = conn.execute("SELECT 1 FROM pg_trigger WHERE tgname=%s AND NOT tgisinternal",
                                      (trigger_name,)).fetchone()
                if not exists:
                    conn.execute(f"""CREATE TRIGGER {trigger_name} AFTER DELETE ON {table} FOR EACH ROW
                      EXECUTE FUNCTION cleanup_study_generation_checkpoints()""")
            # Hibernate-generated enum checks in existing installations must be
            # widened before the worker can update new task types/steps.
            ensure_task_constraints(conn)

    def latest_generating_deck_id(self, document_id: str, user_id: str) -> str:
        return self._latest_id("flashcard_decks", document_id, user_id)

    def latest_generating_quiz_set_id(self, document_id: str, user_id: str) -> str:
        return self._latest_id("quiz_sets", document_id, user_id)

    def load_quiz_generation_options(self, set_id: str) -> dict:
        """Return the user's quiz generation options (empty dict if unset)."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT generation_options_json FROM quiz_sets WHERE id=%s", (set_id,)
            ).fetchone()
        if not row or not row["generation_options_json"]:
            return {}
        try:
            parsed = json.loads(row["generation_options_json"])
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _latest_id(self, table: str, document_id: str, user_id: str) -> str:
        with self.connect() as conn:
            row = conn.execute(f"""SELECT id FROM {table} WHERE document_id=%s AND user_id=%s
              AND status IN ('GENERATING','PARTIAL') ORDER BY version DESC LIMIT 1""",
              (document_id, user_id)).fetchone()
        if not row:
            raise RuntimeError(f"No generating record exists in {table} for document {document_id}.")
        return str(row["id"])

    def assert_document_owner(self, document_id: str, user_id: str) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM documents WHERE id=%s AND user_id=%s AND status='READY'",
                               (document_id, user_id)).fetchone()
        if not row:
            raise PermissionError("Document is not READY or is not owned by the task user.")

    def assert_attempt_owner(self, attempt_id: str, user_id: str) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM quiz_attempts WHERE id=%s AND user_id=%s AND status='GRADING'",
                               (attempt_id, user_id)).fetchone()
        if not row:
            raise PermissionError("Quiz attempt is not GRADING or is not owned by the task user.")

    def acquire_execution_lease(self, lease_key: str, holder_id: str, seconds: int) -> bool:
        with self.connect() as conn:
            row = conn.execute("""INSERT INTO study_execution_leases(lease_key,holder_id,expires_at)
              VALUES (%s,%s,NOW()+(%s::text||' seconds')::interval)
              ON CONFLICT (lease_key) DO UPDATE SET holder_id=EXCLUDED.holder_id,expires_at=EXCLUDED.expires_at,
              updated_at=NOW() WHERE study_execution_leases.expires_at<NOW()
              OR study_execution_leases.holder_id=EXCLUDED.holder_id RETURNING holder_id""",
              (lease_key, holder_id, max(30, seconds))).fetchone()
        return bool(row and str(row["holder_id"]) == holder_id)

    def renew_execution_lease(self, lease_key: str, holder_id: str, seconds: int) -> None:
        with self.connect() as conn:
            row = conn.execute("""UPDATE study_execution_leases SET expires_at=NOW()+(%s::text||' seconds')::interval,
              updated_at=NOW() WHERE lease_key=%s AND holder_id=%s RETURNING holder_id""",
              (max(30, seconds), lease_key, holder_id)).fetchone()
        if not row:
            raise RuntimeError("Study execution lease was lost.")

    def release_execution_lease(self, lease_key: str, holder_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM study_execution_leases WHERE lease_key=%s AND holder_id=%s", (lease_key, holder_id))

    def completed_flashcard_groups(self, deck_id: str) -> set[int]:
        return self._checkpoint_groups("FLASHCARDS", deck_id)

    def completed_quiz_groups(self, quiz_set_id: str) -> set[int]:
        return self._checkpoint_groups("QUIZ", quiz_set_id)

    def _checkpoint_groups(self, generation_type: str, set_id: str) -> set[int]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT source_group_index FROM study_generation_checkpoints
              WHERE generation_type=%s AND set_id=%s AND status='COMPLETED'""", (generation_type, set_id)).fetchall()
        return {row["source_group_index"] for row in rows}

    def save_checkpoint(self, generation_type: str, set_id: str, group_index: int, produced: int,
                        error: str | None = None) -> None:
        status = "FAILED" if error else "COMPLETED"
        with self.connect() as conn:
            conn.execute("""INSERT INTO study_generation_checkpoints
              (generation_type,set_id,source_group_index,status,produced_count,error_message)
              VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (generation_type,set_id,source_group_index) DO UPDATE SET
              status=EXCLUDED.status,produced_count=EXCLUDED.produced_count,error_message=EXCLUDED.error_message,updated_at=NOW()""",
              (generation_type, set_id, group_index, status, produced, error[:2000] if error else None))

    def save_flashcard(self, card: Flashcard) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO flashcards (id,deck_id,document_id,source_group_index,item_index,card_type,front,back,
              cloze_text,difficulty,topic,hint,tags_json,source_chunk_ids_json,source_pages_json,dedupe_hash,confidence,warnings_json,metadata_json)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (deck_id,source_group_index,item_index) DO NOTHING""",
              (card.id, card.deck_id, card.document_id, card.source_group_index, card.item_index, card.card_type,
               card.front, card.back, card.cloze_text, card.difficulty, card.topic, card.hint, json.dumps(card.tags),
               json.dumps(card.source_chunk_ids), json.dumps(card.source_pages), card.dedupe_hash, card.confidence,
               card.warnings_json, card.metadata_json))

    def save_quiz_question(self, question: QuizQuestion) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO quiz_questions (id,quiz_set_id,document_id,source_group_index,item_index,question_type,
              difficulty,topic,stem,options_json,correct_answer,answer_key,rubric_json,explanation,related_formula,
              common_mistake,distractor_rationale_json,points,source_chunk_ids_json,source_pages_json,dedupe_hash,confidence,warnings_json)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (quiz_set_id,source_group_index,item_index) DO NOTHING""",
              (question.id, question.quiz_set_id, question.document_id, question.source_group_index, question.item_index,
               question.question_type, question.difficulty, question.topic, question.stem, question.options_json,
               question.correct_answer, question.answer_key, question.rubric_json, question.explanation,
               question.related_formula, question.common_mistake, question.distractor_rationale_json, question.points,
               json.dumps(question.source_chunk_ids), json.dumps(question.source_pages), question.dedupe_hash,
               question.confidence, question.warnings_json))

    def update_generation(self, table: str, record_id: str, status: str, provider: str, model: str,
                          prompt_version: str, total: int, completed: int, report: dict, error: str | None = None) -> None:
        if table not in {"flashcard_decks", "quiz_sets"}:
            raise ValueError("Unsupported generation table")
        with self.connect() as conn:
            conn.execute(f"""UPDATE {table} SET status=%s,provider=%s,model=%s,prompt_version=%s,total_source_groups=%s,
              completed_source_groups=%s,quality_report_json=%s,error_message=%s,updated_at=NOW() WHERE id=%s""",
              (status, provider, model, prompt_version, total, completed, json.dumps(report, separators=(",", ":")),
               error[:4000] if error else None, record_id))

    def fail_generation(self, table: str, record_id: str, error: str) -> None:
        if table not in {"flashcard_decks", "quiz_sets"}:
            raise ValueError("Unsupported generation table")
        with self.connect() as conn:
            conn.execute(f"""UPDATE {table} SET status=CASE WHEN completed_source_groups>0 THEN 'PARTIAL' ELSE 'FAILED' END,
              error_message=%s,updated_at=NOW() WHERE id=%s AND status NOT IN ('READY')""", (error[:4000], record_id))

    def count_items(self, table: str, key: str, value: str) -> int:
        if (table, key) not in {("flashcards", "deck_id"), ("quiz_questions", "quiz_set_id")}:
            raise ValueError("Unsupported count query")
        with self.connect() as conn:
            return int(conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {key}=%s", (value,)).fetchone()["count"])

    def load_item_texts(self, table: str, key: str, value: str, column: str) -> list[str]:
        allowed = {("flashcards", "deck_id", "front"), ("quiz_questions", "quiz_set_id", "stem")}
        if (table, key, column) not in allowed:
            raise ValueError("Unsupported text query")
        with self.connect() as conn:
            rows = conn.execute(f"SELECT {column} value FROM {table} WHERE {key}=%s", (value,)).fetchall()
        return [row["value"] for row in rows]

    def item_distribution(self, table: str, key: str, value: str, column: str) -> dict[str, int]:
        allowed = {("flashcards", "deck_id", "card_type"), ("flashcards", "deck_id", "difficulty"),
                   ("quiz_questions", "quiz_set_id", "question_type"),
                   ("quiz_questions", "quiz_set_id", "difficulty")}
        if (table, key, column) not in allowed:
            raise ValueError("Unsupported distribution query")
        with self.connect() as conn:
            rows = conn.execute(f"SELECT {column} value,COUNT(*) count FROM {table} WHERE {key}=%s GROUP BY {column}",
                                (value,)).fetchall()
        return {row["value"]: int(row["count"]) for row in rows}

    def load_answers_to_grade(self, attempt_id: str) -> list[QuizAnswerToGrade]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT a.id answer_id,a.user_response,q.* FROM quiz_answers a
              JOIN quiz_questions q ON q.id=a.question_id WHERE a.attempt_id=%s AND a.graded_by IS NULL
              AND q.question_type IN ('CONCEPTUAL','CALCULATION','PROOF','SHORT_ANSWER') ORDER BY q.item_index""",
              (attempt_id,)).fetchall()
        return [QuizAnswerToGrade(str(row["answer_id"]), self._question(row), row["user_response"]) for row in rows]

    @staticmethod
    def _question(row) -> QuizQuestion:
        return QuizQuestion(str(row["id"]), str(row["quiz_set_id"]), str(row["document_id"]), row["item_index"],
            row["question_type"], row["difficulty"], row["topic"], row["stem"], row["options_json"],
            row["correct_answer"], row["answer_key"], row["rubric_json"], row["explanation"], row["related_formula"],
            row["common_mistake"], row["distractor_rationale_json"], row["points"], row["source_group_index"],
            row["item_index"], json.loads(row["source_chunk_ids_json"]), json.loads(row["source_pages_json"]),
            row["dedupe_hash"], row["confidence"], row["warnings_json"])

    def save_grade(self, answer_id: str, result) -> None:
        with self.connect() as conn:
            conn.execute("""UPDATE quiz_answers SET is_correct=%s,awarded_points=%s,feedback=%s,key_points_hit_json=%s,
              graded_by=%s,grading_error=NULL,updated_at=NOW() WHERE id=%s AND graded_by IS NULL""",
              (result.is_correct, result.awarded_points, result.feedback, json.dumps(result.key_points_hit),
               result.graded_by, answer_id))

    def fail_grade(self, answer_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE quiz_answers SET grading_error=%s,updated_at=NOW() WHERE id=%s AND graded_by IS NULL",
                         (error[:2000], answer_id))

    def save_attempt_grading_usage(self, attempt_id: str, usage: dict) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE quiz_attempts SET grading_usage_json=%s,updated_at=NOW() WHERE id=%s",
                         (json.dumps(usage, separators=(",", ":")), attempt_id))

    def complete_attempt_if_graded(self, attempt_id: str) -> tuple[float, float, int]:
        with self.connect() as conn:
            row = conn.execute("""SELECT COALESCE(SUM(a.awarded_points),0) score,COALESCE(SUM(q.points),0) max_score,
              COUNT(*) FILTER (WHERE a.graded_by IS NULL) remaining FROM quiz_answers a
              JOIN quiz_questions q ON q.id=a.question_id WHERE a.attempt_id=%s""", (attempt_id,)).fetchone()
            if row["remaining"] == 0:
                topics = conn.execute("""SELECT q.topic,COALESCE(SUM(a.awarded_points),0) score,SUM(q.points) maximum
                  FROM quiz_answers a JOIN quiz_questions q ON q.id=a.question_id WHERE a.attempt_id=%s
                  GROUP BY q.topic ORDER BY CASE WHEN SUM(q.points)>0 THEN COALESCE(SUM(a.awarded_points),0)/SUM(q.points) ELSE 1 END,
                  q.topic""", (attempt_id,)).fetchall()
                weak_topics = [{"topic": topic["topic"], "scoreRatio": round(float(topic["score"]) / float(topic["maximum"]), 4)}
                               for topic in topics if topic["maximum"] and float(topic["score"]) / float(topic["maximum"]) < 0.7]
                conn.execute("""UPDATE quiz_attempts SET status='COMPLETED',score=%s,max_score=%s,
                  weak_topics_json=%s,completed_at=NOW(),updated_at=NOW() WHERE id=%s""",
                  (row["score"], row["max_score"], json.dumps(weak_topics), attempt_id))
        return float(row["score"]), float(row["max_score"]), int(row["remaining"])

    def recover_stale_study_tasks(self, stale_after_minutes: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("""WITH stale AS (SELECT id FROM tasks WHERE task_type IN
              ('GENERATE_FLASHCARDS','GENERATE_QUIZ','GRADE_QUIZ_ATTEMPT') AND status='PROCESSING'
              AND updated_at < NOW()-(%s::text||' minutes')::interval FOR UPDATE SKIP LOCKED)
              UPDATE tasks t SET status='RETRYING',retry_count=retry_count+1,error_message='Recovered stale study task.',
              updated_at=NOW() FROM stale WHERE t.id=stale.id RETURNING t.id,t.document_id,t.user_id,t.task_type,
              (SELECT attempt_id FROM study_task_targets st WHERE st.task_id=t.id) attempt_id""",
              (stale_after_minutes,)).fetchall()
        return [dict(row) for row in rows]

    def bind_task_attempt(self, task_id: str, attempt_id: str) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO study_task_targets(task_id,attempt_id) VALUES (%s,%s)
              ON CONFLICT (task_id) DO UPDATE SET attempt_id=EXCLUDED.attempt_id""", (task_id, attempt_id))

    def load_review_state(self, user_id: str, flashcard_id: str, initial_ease: float) -> ReviewState:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM flashcard_review_states WHERE user_id=%s AND flashcard_id=%s",
                               (user_id, flashcard_id)).fetchone()
        if not row:
            return ReviewState(user_id, flashcard_id, "NEW", initial_ease, 0, 0, None, None, None)
        return ReviewState(str(row["user_id"]), str(row["flashcard_id"]), row["status"], row["ease_factor"],
                           row["interval_days"], row["repetitions"], row["due_at"], row["last_reviewed_at"], row["last_grade"])

    def save_review_state(self, state: ReviewState) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO flashcard_review_states (user_id,flashcard_id,status,ease_factor,interval_days,
              repetitions,due_at,last_reviewed_at,last_grade) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (user_id,flashcard_id) DO UPDATE SET status=EXCLUDED.status,ease_factor=EXCLUDED.ease_factor,
              interval_days=EXCLUDED.interval_days,repetitions=EXCLUDED.repetitions,due_at=EXCLUDED.due_at,
              last_reviewed_at=EXCLUDED.last_reviewed_at,last_grade=EXCLUDED.last_grade,updated_at=NOW()""",
              (state.user_id, state.flashcard_id, state.status, state.ease_factor, state.interval_days,
               state.repetitions, state.due_at, state.last_reviewed_at, state.last_grade))

    def load_due_flashcards(self, user_id: str, deck_id: str, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT f.*,COALESCE(s.status,'NEW') review_status,s.due_at,s.ease_factor,
              COALESCE(s.interval_days,0) interval_days,COALESCE(s.repetitions,0) repetitions
              FROM flashcards f LEFT JOIN flashcard_review_states s ON s.flashcard_id=f.id AND s.user_id=%s
              WHERE f.deck_id=%s AND (s.flashcard_id IS NULL OR (s.status<>'SUSPENDED' AND s.due_at<=NOW()))
              ORDER BY s.due_at NULLS FIRST,f.item_index LIMIT %s""", (user_id, deck_id, max(1, min(limit, 500)))).fetchall()
        return [dict(row) for row in rows]
