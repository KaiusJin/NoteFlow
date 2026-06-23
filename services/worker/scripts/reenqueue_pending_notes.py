from __future__ import annotations

import json
import sys
from pathlib import Path

import redis

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.config import settings
from noteflow_worker.db.repository import Repository


def main() -> int:
    repository = Repository()
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
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
        payload = {
            "taskId": str(row["id"]),
            "documentId": str(row["document_id"]),
            "userId": str(row["user_id"]),
            "taskType": row["task_type"],
        }
        redis_client.rpush(settings.document_queue, json.dumps(payload, separators=(",", ":")))
        print(f"Re-enqueued notes task {payload['taskId']} for document {payload['documentId']}")

    print(f"Re-enqueued {len(rows)} pending notes task(s) to {settings.document_queue}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
