import json
from dataclasses import dataclass
from typing import Optional

import redis

from noteflow_worker.config import settings


@dataclass(frozen=True)
class TaskPayload:
    task_id: str
    document_id: str
    user_id: str
    task_type: str


class RedisTaskQueue:
    def __init__(self) -> None:
        self._client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    def pop(self) -> Optional[TaskPayload]:
        item = self._client.blpop(settings.document_queue, timeout=settings.block_timeout_seconds)
        if item is None:
            return None
        _, raw_payload = item
        payload = json.loads(raw_payload)
        return TaskPayload(
            task_id=payload["taskId"],
            document_id=payload["documentId"],
            user_id=payload["userId"],
            task_type=payload["taskType"],
        )

    def push(self, payload: TaskPayload) -> None:
        raw_payload = json.dumps(
            {
                "taskId": payload.task_id,
                "documentId": payload.document_id,
                "userId": payload.user_id,
                "taskType": payload.task_type,
            },
            separators=(",", ":"),
        )
        self._client.rpush(settings.document_queue, raw_payload)
