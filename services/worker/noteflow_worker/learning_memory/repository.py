from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from noteflow_worker.db.repository import Repository


class LearningMemoryRepository(Repository):
    """Append events and atomically maintain small, indexable topic read models."""

    _schema_ready = False
    _schema_lock = Lock()

    def ensure_schema(self) -> None:
        if self.__class__._schema_ready:
            return
        with self.__class__._schema_lock:
            if self.__class__._schema_ready:
                return
            self._create_schema()
            self.__class__._schema_ready = True

    def _create_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS learning_events (
              id UUID PRIMARY KEY, workspace_id UUID NOT NULL, scope_id UUID NOT NULL,
              external_event_id VARCHAR(256) NOT NULL, event_type VARCHAR(48) NOT NULL,
              topic_key VARCHAR(512) NOT NULL, topic TEXT NOT NULL, document_id UUID,
              artifact_type VARCHAR(32), artifact_id UUID, correct BOOLEAN, difficulty VARCHAR(16),
              response_time_ms INTEGER, hint_used BOOLEAN NOT NULL DEFAULT FALSE, review_grade VARCHAR(16),
              mistake_type VARCHAR(48), mistake_summary TEXT, metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              occurred_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              UNIQUE(workspace_id,external_event_id,topic_key))""",
            """CREATE TABLE IF NOT EXISTS topic_learning_memory (
              workspace_id UUID NOT NULL, scope_id UUID NOT NULL, topic_key VARCHAR(512) NOT NULL, topic TEXT NOT NULL,
              mastery DOUBLE PRECISION NOT NULL DEFAULT 0.5, confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
              evidence_weight DOUBLE PRECISION NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
              correct_count INTEGER NOT NULL DEFAULT 0, incorrect_count INTEGER NOT NULL DEFAULT 0,
              hint_count INTEGER NOT NULL DEFAULT 0, total_response_time_ms BIGINT NOT NULL DEFAULT 0,
              consecutive_correct INTEGER NOT NULL DEFAULT 0, consecutive_incorrect INTEGER NOT NULL DEFAULT 0,
              recent_trend DOUBLE PRECISION NOT NULL DEFAULT 0, last_activity_at TIMESTAMPTZ,
              last_reviewed_at TIMESTAMPTZ, next_review_at TIMESTAMPTZ,
              needs_review BOOLEAN NOT NULL DEFAULT FALSE, version BIGINT NOT NULL DEFAULT 1,
              updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(workspace_id,scope_id,topic_key))""",
            """CREATE TABLE IF NOT EXISTS mistake_memory (
              workspace_id UUID NOT NULL, scope_id UUID NOT NULL, topic_key VARCHAR(512) NOT NULL,
              mistake_fingerprint VARCHAR(128) NOT NULL, topic TEXT NOT NULL, mistake_type VARCHAR(48) NOT NULL,
              summary TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1, first_seen_at TIMESTAMPTZ NOT NULL,
              last_seen_at TIMESTAMPTZ NOT NULL, last_event_id UUID NOT NULL, version BIGINT NOT NULL DEFAULT 1,
              PRIMARY KEY(workspace_id,scope_id,topic_key,mistake_fingerprint))""",
            "CREATE INDEX IF NOT EXISTS idx_learning_events_workspace_time ON learning_events(workspace_id,occurred_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_learning_events_artifact ON learning_events(workspace_id,artifact_type,artifact_id)",
            "CREATE INDEX IF NOT EXISTS idx_topic_memory_weak ON topic_learning_memory(workspace_id,needs_review,mastery,next_review_at)",
            "CREATE INDEX IF NOT EXISTS idx_topic_memory_due ON topic_learning_memory(workspace_id,next_review_at) WHERE needs_review",
            "CREATE INDEX IF NOT EXISTS idx_mistake_memory_rank ON mistake_memory(workspace_id,occurrences DESC,last_seen_at DESC)",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS lapse_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS stability_days DOUBLE PRECISION NOT NULL DEFAULT 1",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS calibration_error DOUBLE PRECISION NOT NULL DEFAULT 0",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS easy_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS medium_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS hard_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE topic_learning_memory ADD COLUMN IF NOT EXISTS response_time_count INTEGER NOT NULL DEFAULT 0",
            """CREATE TABLE IF NOT EXISTS learning_memory_history (
              id UUID PRIMARY KEY,workspace_id UUID NOT NULL,scope_id UUID NOT NULL,topic_key VARCHAR(512) NOT NULL,
              source_event_id UUID,mastery DOUBLE PRECISION NOT NULL,confidence DOUBLE PRECISION NOT NULL,
              attempts INTEGER NOT NULL,recent_trend DOUBLE PRECISION NOT NULL,algorithm_version VARCHAR(32) NOT NULL,
              recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
            "CREATE INDEX IF NOT EXISTS idx_learning_history_topic ON learning_memory_history(workspace_id,topic_key,recorded_at DESC)",
            """CREATE TABLE IF NOT EXISTS learning_artifact_links (
              workspace_id UUID NOT NULL,topic_key VARCHAR(512) NOT NULL,artifact_type VARCHAR(32) NOT NULL,
              artifact_id UUID NOT NULL,title TEXT NOT NULL DEFAULT '',document_id UUID,status VARCHAR(24) NOT NULL DEFAULT 'ACTIVE',
              interaction_count INTEGER NOT NULL DEFAULT 0,last_interacted_at TIMESTAMPTZ,metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
              created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY(workspace_id,topic_key,artifact_type,artifact_id))""",
            """CREATE TABLE IF NOT EXISTS learning_topic_edges (
              workspace_id UUID NOT NULL,from_topic_key VARCHAR(512) NOT NULL,to_topic_key VARCHAR(512) NOT NULL,
              relation VARCHAR(32) NOT NULL,weight DOUBLE PRECISION NOT NULL DEFAULT .5,source VARCHAR(32) NOT NULL,
              evidence_count INTEGER NOT NULL DEFAULT 1,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY(workspace_id,from_topic_key,to_topic_key,relation))""",
            """CREATE TABLE IF NOT EXISTS learning_preferences (
              workspace_id UUID NOT NULL,preference_key VARCHAR(128) NOT NULL,value_json JSONB NOT NULL,
              source VARCHAR(16) NOT NULL,confidence DOUBLE PRECISION NOT NULL,evidence_count INTEGER NOT NULL DEFAULT 1,
              version BIGINT NOT NULL DEFAULT 1,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              PRIMARY KEY(workspace_id,preference_key))""",
        ]
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('noteflow-learning-memory-schema-v1'))")
            for statement in statements:
                conn.execute(statement)

    def record_quiz_attempt(self, attempt_id: str, workspace_id: str) -> int:
        """Record every graded answer once; safe after duplicate task delivery."""
        now = datetime.now(timezone.utc)
        accepted = 0
        with self.connect() as conn:
            answers = conn.execute("""SELECT ans.id,ans.is_correct,ans.response_time_ms,ans.hint_used,q.id question_id,q.topic,q.difficulty,
                q.common_mistake,s.id quiz_set_id,s.document_id FROM quiz_answers ans
                JOIN quiz_questions q ON q.id=ans.question_id
                JOIN quiz_attempts a ON a.id=ans.attempt_id JOIN quiz_sets s ON s.id=a.quiz_set_id
                WHERE a.id=%s AND a.user_id=%s AND ans.graded_by IS NOT NULL""",
                (attempt_id, workspace_id)).fetchall()
            lock_keys = sorted({(str(answer["document_id"]), self.topic_key(str(answer["topic"]))) for answer in answers})
            for scope_id, topic_key in lock_keys:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                             (f"learning-memory:{workspace_id}:{scope_id}:{topic_key}",))
            for answer in answers:
                if self._record_answer(conn, answer, attempt_id, workspace_id, now):
                    accepted += 1
        return accepted

    def sync_artifact_topics(self, artifact_type: str, artifact_id: str, workspace_id: str) -> int:
        if artifact_type == "QUIZ":
            meta_sql = "SELECT title,document_id FROM quiz_sets WHERE id=%s AND user_id=%s"
            topics_sql = "SELECT DISTINCT topic FROM quiz_questions WHERE quiz_set_id=%s"
        elif artifact_type == "FLASHCARDS":
            meta_sql = "SELECT title,document_id FROM flashcard_decks WHERE id=%s AND user_id=%s"
            topics_sql = "SELECT DISTINCT topic FROM flashcards WHERE deck_id=%s"
        else:
            raise ValueError("Unsupported learning artifact type")
        self.ensure_schema()
        with self.connect() as conn:
            meta = conn.execute(meta_sql, (artifact_id, workspace_id)).fetchone()
            if not meta:
                return 0
            topics = [str(row["topic"]).strip() for row in conn.execute(topics_sql, (artifact_id,)).fetchall() if row["topic"]]
            keys = list(dict.fromkeys(self.topic_key(topic) for topic in topics))
            for key in keys:
                conn.execute("""INSERT INTO learning_artifact_links(workspace_id,topic_key,artifact_type,artifact_id,title,document_id)
                  VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,topic_key,artifact_type,artifact_id) DO UPDATE SET
                  title=EXCLUDED.title,status='ACTIVE',updated_at=NOW()""",
                  (workspace_id,key,artifact_type,artifact_id,meta["title"],meta["document_id"]))
            for left in keys:
                for right in keys:
                    if left == right:
                        continue
                    conn.execute("""INSERT INTO learning_topic_edges(workspace_id,from_topic_key,to_topic_key,relation,weight,source)
                      VALUES (%s,%s,%s,'CO_OCCURS',.4,'ARTIFACT') ON CONFLICT(workspace_id,from_topic_key,to_topic_key,relation)
                      DO UPDATE SET evidence_count=learning_topic_edges.evidence_count+1,
                      weight=LEAST(1,learning_topic_edges.weight+.02),updated_at=NOW()""",(workspace_id,left,right))
        return len(keys)

    def _record_answer(self, conn, answer, attempt_id: str, workspace_id: str, occurred_at: datetime) -> bool:
        topic = str(answer["topic"]).strip()
        key = self.topic_key(topic)
        event_row_id = str(uuid4())
        inserted = conn.execute("""INSERT INTO learning_events(id,workspace_id,scope_id,external_event_id,event_type,
            topic_key,topic,document_id,artifact_type,artifact_id,correct,difficulty,response_time_ms,hint_used,mistake_type,
            mistake_summary,metadata_json,occurred_at) VALUES (%s,%s,%s,%s,'QUESTION_ANSWERED',%s,%s,%s,
            'QUIZ',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT(workspace_id,external_event_id,topic_key) DO NOTHING RETURNING id""",
            (event_row_id, workspace_id, answer["document_id"], f"quiz-answer:{answer['id']}:v1", key, topic,
             answer["document_id"], answer["quiz_set_id"], answer["is_correct"], answer["difficulty"],
             answer.get("response_time_ms"), bool(answer.get("hint_used")),
             None if answer["is_correct"] else "UNCLASSIFIED",
             None if answer["is_correct"] else (answer["common_mistake"] or "Incorrect quiz answer"),
             json.dumps({"attemptId": attempt_id,"questionId":str(answer["question_id"])}, separators=(",", ":")), occurred_at)).fetchone()
        if not inserted:
            return False

        conn.execute("""UPDATE learning_artifact_links SET interaction_count=interaction_count+1,
          last_interacted_at=GREATEST(COALESCE(last_interacted_at,%s),%s),updated_at=NOW()
          WHERE workspace_id=%s AND topic_key=%s AND artifact_type='QUIZ' AND artifact_id=%s""",
          (occurred_at,occurred_at,workspace_id,key,answer["quiz_set_id"]))
        preference_key = "practice_format_topic:" + hashlib.sha256(key.encode()).hexdigest()[:64]
        if int(hashlib.sha256(f"quiz-answer:{answer['id']}:v1".encode()).hexdigest()[:8],16) % 5 == 0:
            conn.execute("""INSERT INTO learning_preferences(workspace_id,preference_key,value_json,source,confidence,evidence_count)
          VALUES (%s,%s,'"QUIZ"'::jsonb,'BEHAVIOR',.1,1)
          ON CONFLICT(workspace_id,preference_key) DO UPDATE SET
          value_json=CASE WHEN learning_preferences.source='EXPLICIT' THEN learning_preferences.value_json ELSE EXCLUDED.value_json END,
          evidence_count=CASE WHEN learning_preferences.source='EXPLICIT' THEN learning_preferences.evidence_count
            WHEN learning_preferences.value_json=EXCLUDED.value_json THEN learning_preferences.evidence_count+1 ELSE 1 END,
          confidence=CASE WHEN learning_preferences.source='EXPLICIT' THEN 1
            WHEN learning_preferences.value_json=EXCLUDED.value_json THEN LEAST(.95,(learning_preferences.evidence_count+1)/10.0) ELSE .1 END,
          version=learning_preferences.version+1,updated_at=NOW()""",(workspace_id,preference_key))

        correct = bool(answer["is_correct"])
        hint_used = bool(answer.get("hint_used"))
        multiplier = {"EASY": .75, "HARD": 1.35}.get(answer["difficulty"], 1.0)
        delta = (.06 if correct else -.10) * multiplier * (.55 if hint_used and correct else 1.0)
        weight = multiplier * (.65 if hint_used else 1.0)
        initial_mastery = max(0.0, min(1.0, .5 + delta))
        initial_stability = min(365.0, 1.0 + max(1.0, weight * 2.0)) if correct else 1.0
        initial_calibration = .1
        initial_confidence = max(0.0, min(1.0, weight * .08) - .02)
        easy_attempts = 1 if answer["difficulty"] == "EASY" else 0
        medium_attempts = 1 if answer["difficulty"] == "MEDIUM" else 0
        hard_attempts = 1 if answer["difficulty"] == "HARD" else 0
        hint_count = 1 if answer.get("hint_used") else 0
        response_ms = int(answer["response_time_ms"] or 0)
        response_count = 1 if answer.get("response_time_ms") is not None else 0
        next_review = occurred_at + timedelta(days=5 if correct else 1)
        state = conn.execute("""INSERT INTO topic_learning_memory(workspace_id,scope_id,topic_key,topic,mastery,confidence,
            evidence_weight,attempts,correct_count,incorrect_count,hint_count,total_response_time_ms,response_time_count,
            consecutive_correct,consecutive_incorrect,
            recent_trend,last_activity_at,last_reviewed_at,next_review_at,needs_review,lapse_count,stability_days,
            calibration_error,is_active,easy_attempts,medium_attempts,hard_attempts)
            VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s,%s)
            ON CONFLICT(workspace_id,scope_id,topic_key) DO UPDATE SET topic=EXCLUDED.topic,
            is_active=TRUE,
            mastery=GREATEST(0,LEAST(1,topic_learning_memory.mastery+%s)),
            confidence=GREATEST(0,LEAST(1,topic_learning_memory.confidence+%s-
              ABS(topic_learning_memory.mastery-CASE WHEN EXCLUDED.correct_count>0 THEN 1 ELSE 0 END)*.04)),
            evidence_weight=topic_learning_memory.evidence_weight+EXCLUDED.evidence_weight,
            attempts=topic_learning_memory.attempts+1,
            correct_count=topic_learning_memory.correct_count+EXCLUDED.correct_count,
            incorrect_count=topic_learning_memory.incorrect_count+EXCLUDED.incorrect_count,
            easy_attempts=topic_learning_memory.easy_attempts+EXCLUDED.easy_attempts,
            medium_attempts=topic_learning_memory.medium_attempts+EXCLUDED.medium_attempts,
            hard_attempts=topic_learning_memory.hard_attempts+EXCLUDED.hard_attempts,
            hint_count=topic_learning_memory.hint_count+EXCLUDED.hint_count,
            total_response_time_ms=topic_learning_memory.total_response_time_ms+EXCLUDED.total_response_time_ms,
            response_time_count=topic_learning_memory.response_time_count+EXCLUDED.response_time_count,
            consecutive_correct=CASE WHEN EXCLUDED.correct_count>0 THEN topic_learning_memory.consecutive_correct+1 ELSE 0 END,
            consecutive_incorrect=CASE WHEN EXCLUDED.incorrect_count>0 THEN topic_learning_memory.consecutive_incorrect+1 ELSE 0 END,
            recent_trend=topic_learning_memory.recent_trend*.7+EXCLUDED.recent_trend*.3,
            lapse_count=topic_learning_memory.lapse_count+EXCLUDED.incorrect_count,
            stability_days=CASE WHEN EXCLUDED.incorrect_count>0 THEN GREATEST(1,topic_learning_memory.stability_days*.55)
              WHEN EXCLUDED.correct_count>0 THEN LEAST(365,topic_learning_memory.stability_days+GREATEST(1,EXCLUDED.evidence_weight*2))
              ELSE topic_learning_memory.stability_days END,
            calibration_error=topic_learning_memory.calibration_error*.8+
              ABS(topic_learning_memory.mastery-CASE WHEN EXCLUDED.correct_count>0 THEN 1 ELSE 0 END)*.2,
            last_activity_at=GREATEST(topic_learning_memory.last_activity_at,EXCLUDED.last_activity_at),
            last_reviewed_at=GREATEST(topic_learning_memory.last_reviewed_at,EXCLUDED.last_reviewed_at),
            next_review_at=CASE WHEN EXCLUDED.last_activity_at>=topic_learning_memory.last_activity_at
              THEN CASE WHEN EXCLUDED.correct_count>0 THEN EXCLUDED.last_activity_at+
                make_interval(days=>CEIL(LEAST(365,topic_learning_memory.stability_days+GREATEST(1,EXCLUDED.evidence_weight*2)))::integer)
                ELSE EXCLUDED.next_review_at END ELSE topic_learning_memory.next_review_at END,
            needs_review=(GREATEST(0,LEAST(1,topic_learning_memory.mastery+%s))<.7 OR EXCLUDED.incorrect_count>0),
            version=topic_learning_memory.version+1,updated_at=NOW()
            RETURNING mastery,confidence,attempts,recent_trend""",
            (workspace_id, answer["document_id"], key, topic, initial_mastery, initial_confidence, weight,
             1 if correct else 0, 0 if correct else 1,hint_count,response_ms,response_count,
             1 if correct else 0, 0 if correct else 1,
             1 if correct else -1, occurred_at, occurred_at, next_review, initial_mastery < .7 or not correct,
             0 if correct else 1, initial_stability, initial_calibration,easy_attempts,medium_attempts,hard_attempts,
             delta, weight * .08, delta)).fetchone()
        conn.execute("""INSERT INTO learning_memory_history(id,workspace_id,scope_id,topic_key,source_event_id,
          mastery,confidence,attempts,recent_trend,algorithm_version,recorded_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'v1',%s)""",
          (str(uuid4()),workspace_id,answer["document_id"],key,event_row_id,state["mastery"],state["confidence"],
           state["attempts"],state["recent_trend"],occurred_at))
        if not correct:
            summary = answer["common_mistake"] or "Incorrect quiz answer"
            mistake_type = "UNCLASSIFIED"
            fingerprint = hashlib.sha256(f"{mistake_type}\n{summary.lower()}".encode()).hexdigest()
            conn.execute("""INSERT INTO mistake_memory(workspace_id,scope_id,topic_key,mistake_fingerprint,topic,
                mistake_type,summary,first_seen_at,last_seen_at,last_event_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(workspace_id,scope_id,topic_key,mistake_fingerprint) DO UPDATE SET
                occurrences=mistake_memory.occurrences+1,last_seen_at=GREATEST(mistake_memory.last_seen_at,EXCLUDED.last_seen_at),
                last_event_id=EXCLUDED.last_event_id,version=mistake_memory.version+1""",
                (workspace_id, answer["document_id"], key, fingerprint, topic, mistake_type, summary,
                 occurred_at, occurred_at, event_row_id))
        return True

    @staticmethod
    def topic_key(topic: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", topic).strip().lower().split())
