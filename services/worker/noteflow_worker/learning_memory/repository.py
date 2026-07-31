from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import uuid4

from noteflow_worker.db.repository import Repository
from noteflow_worker.db.schema import require_tables


class LearningMemoryRepository(Repository):
    """Append events and atomically maintain small, indexable topic read models."""

    _schema_ready = False
    _schema_lock = Lock()

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            require_tables(
                conn,
                (
                    "learning_events",
                    "topic_learning_memory",
                    "mistake_memory",
                    "learning_memory_history",
                    "learning_artifact_links",
                    "learning_topic_edges",
                    "learning_preferences",
                ),
            )

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
