from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.db.repository import Repository
from noteflow_worker.queue.redis_queue import RedisTaskQueue, TaskPayload


def main() -> int:
    repository = Repository()
    queue = RedisTaskQueue()
    with repository.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, document_id, user_id, task_type
            FROM tasks
            WHERE task_type = 'GENERATE_NOTES'
              AND status IN ('PENDING', 'RETRYING')
            ORDER BY created_at
            """
        ).fetchall()

    for row in rows:
        payload = TaskPayload(
            task_id=str(row["id"]),
            document_id=str(row["document_id"]),
            user_id=str(row["user_id"]),
            task_type=row["task_type"],
        )
        queue.push(payload)
        print(f"Re-enqueued notes task {payload.task_id} for document {payload.document_id}")

    print(f"Re-enqueued {len(rows)} pending notes task(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
