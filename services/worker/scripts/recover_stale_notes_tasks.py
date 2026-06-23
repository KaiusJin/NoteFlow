from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.config import settings
from noteflow_worker.db.repository import Repository
from noteflow_worker.queue.redis_queue import RedisTaskQueue, TaskPayload


def main() -> int:
    repository = Repository()
    queue = RedisTaskQueue()
    rows = repository.recover_stale_generate_notes_tasks(settings.notes_stale_task_after_minutes)

    for row in rows:
        payload = TaskPayload(
            task_id=str(row["id"]),
            document_id=str(row["document_id"]),
            user_id=str(row["user_id"]),
            task_type=row["task_type"],
        )
        queue.push(payload)
        print(f"Recovered stale notes task {payload.task_id} for document {payload.document_id}")

    print(
        f"Recovered {len(rows)} stale notes task(s) older than "
        f"{settings.notes_stale_task_after_minutes} minutes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
