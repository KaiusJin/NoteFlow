#!/usr/bin/env python3
"""Local PostgreSQL benchmark for the Learning Memory hot paths.

Creates isolated fixtures, drives all writes into one topic (the contention
worst case), verifies exact counts after a duplicate-delivery storm, and cleans
up every row it creates.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from noteflow_worker.learning_memory import LearningMemoryRepository
from noteflow_worker.study.repository import StudyRepository


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=500)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--reads", type=int, default=2000)
    args = parser.parse_args()
    events, workers = max(1, args.events), max(1, args.workers)
    study, memory = StudyRepository(), LearningMemoryRepository()
    study.ensure_study_schema(); memory.ensure_schema()
    workspace_id, document_id, quiz_id, question_id = (str(uuid4()) for _ in range(4))
    attempts = [str(uuid4()) for _ in range(events)]
    try:
        with study.connect() as conn:
            if conn.execute("SELECT to_regclass('users') present").fetchone()["present"]:
                conn.execute("INSERT INTO users(id,display_name,email,created_at,updated_at) VALUES (%s,'Benchmark','benchmark@local',NOW(),NOW()) ON CONFLICT(id) DO NOTHING", (workspace_id,))
            conn.execute("""INSERT INTO documents(id,user_id,title,storage_path,file_size,status,document_type,content_source_type)
              VALUES (%s,%s,'Memory benchmark','/tmp/benchmark.pdf',1,'READY','COURSE_NOTES','TEXT_PDF')""",
              (document_id, workspace_id))
            conn.execute("""INSERT INTO quiz_sets(id,document_id,user_id,version,title,difficulty_distribution_json,status)
              VALUES (%s,%s,%s,1,'Memory benchmark','{}','READY')""", (quiz_id, document_id, workspace_id))
            conn.execute("""INSERT INTO quiz_questions(id,quiz_set_id,document_id,source_group_index,item_index,
              question_type,difficulty,topic,stem,correct_answer,answer_key,rubric_json,explanation,common_mistake,
              points,source_chunk_ids_json,source_pages_json,dedupe_hash,confidence)
              VALUES (%s,%s,%s,0,0,'TRUE_FALSE','HARD','Contention Topic','True?','TRUE','TRUE','[]','Explanation',
              'Repeated misconception',1,'[]','[]',%s,.99)""", (question_id, quiz_id, document_id, "e" * 64))
            for index, attempt_id in enumerate(attempts):
                conn.execute("INSERT INTO quiz_attempts(id,quiz_set_id,user_id,status) VALUES (%s,%s,%s,'COMPLETED')",
                             (attempt_id, quiz_id, workspace_id))
                conn.execute("""INSERT INTO quiz_answers(id,attempt_id,question_id,user_response,is_correct,
                  awarded_points,graded_by) VALUES (%s,%s,%s,%s,%s,%s,'AUTO')""",
                  (str(uuid4()), attempt_id, question_id, "TRUE" if index % 2 else "FALSE",
                   index % 2 == 1, 1 if index % 2 else 0))

        def write(attempt_id: str) -> float:
            start = time.perf_counter()
            memory.record_quiz_attempt(attempt_id, workspace_id)
            return (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            write_ms = list(pool.map(write, attempts))
        write_seconds = time.perf_counter() - start

        duplicate_ids = [attempts[0]] * events
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            duplicate_ms = list(pool.map(write, duplicate_ids))
        duplicate_seconds = time.perf_counter() - start

        def read() -> float:
            start = time.perf_counter()
            with memory.connect() as conn:
                conn.execute("""SELECT topic,mastery,confidence,attempts,incorrect_count,next_review_at
                  FROM topic_learning_memory WHERE workspace_id=%s ORDER BY mastery LIMIT 20""",
                  (workspace_id,)).fetchall()
            return (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            read_ms = list(pool.map(lambda _: read(), range(max(1, args.reads))))
        read_seconds = time.perf_counter() - start
        with memory.connect() as conn:
            state = conn.execute("SELECT attempts,version FROM topic_learning_memory WHERE workspace_id=%s",
                                 (workspace_id,)).fetchone()
            event_count = conn.execute("SELECT COUNT(*) count FROM learning_events WHERE workspace_id=%s",
                                       (workspace_id,)).fetchone()["count"]
            history_count = conn.execute("SELECT COUNT(*) count FROM learning_memory_history WHERE workspace_id=%s",
                                         (workspace_id,)).fetchone()["count"]
        if int(state["attempts"]) != events or int(state["version"]) != events or int(event_count) != events or int(history_count)!=events:
            raise RuntimeError(f"idempotency failure: events={event_count}, state={dict(state)}")
        print(json.dumps({
            "events": events, "workers": workers,
            "uniqueWrites": {"throughputPerSecond": round(events / write_seconds, 1),
                             "p50Ms": round(percentile(write_ms, .50), 2), "p95Ms": round(percentile(write_ms, .95), 2)},
            "duplicateWrites": {"throughputPerSecond": round(events / duplicate_seconds, 1),
                                "p50Ms": round(percentile(duplicate_ms, .50), 2), "p95Ms": round(percentile(duplicate_ms, .95), 2)},
            "profileReads": {"throughputPerSecond": round(max(1, args.reads) / read_seconds, 1),
                             "p50Ms": round(percentile(read_ms, .50), 2), "p95Ms": round(percentile(read_ms, .95), 2)},
            "verifiedEventCount": event_count, "verifiedHistoryCount": history_count,
            "verifiedTopicAttempts": state["attempts"]
        }, indent=2))
    finally:
        with study.connect() as conn:
            conn.execute("DELETE FROM learning_events WHERE workspace_id=%s", (workspace_id,))
            conn.execute("DELETE FROM topic_learning_memory WHERE workspace_id=%s", (workspace_id,))
            conn.execute("DELETE FROM mistake_memory WHERE workspace_id=%s", (workspace_id,))
            conn.execute("DELETE FROM learning_memory_history WHERE workspace_id=%s", (workspace_id,))
            conn.execute("DELETE FROM learning_artifact_links WHERE workspace_id=%s", (workspace_id,))
            conn.execute("DELETE FROM learning_topic_edges WHERE workspace_id=%s", (workspace_id,))
            conn.execute("DELETE FROM quiz_sets WHERE id=%s", (quiz_id,))
            conn.execute("DELETE FROM documents WHERE id=%s", (document_id,))
            if conn.execute("SELECT to_regclass('users') present").fetchone()["present"]:
                conn.execute("DELETE FROM users WHERE id=%s", (workspace_id,))


if __name__ == "__main__":
    main()
