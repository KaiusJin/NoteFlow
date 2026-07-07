import json
import time
from dataclasses import dataclass
from typing import Optional

import redis

from noteflow_worker.config import settings


PRIORITY_INTERACTIVE = 0
PRIORITY_USER_VISIBLE = 1
PRIORITY_BACKGROUND = 2
ALL_PRIORITIES = (PRIORITY_INTERACTIVE, PRIORITY_USER_VISIBLE, PRIORITY_BACKGROUND)

# Weighted round-robin prevents background starvation while still giving 75%
# of immediate dequeue opportunities to interactive/user-visible work.
PRIORITY_SCHEDULE = (
    PRIORITY_INTERACTIVE,
    PRIORITY_USER_VISIBLE,
    PRIORITY_INTERACTIVE,
    PRIORITY_USER_VISIBLE,
    PRIORITY_INTERACTIVE,
    PRIORITY_BACKGROUND,
    PRIORITY_USER_VISIBLE,
    PRIORITY_BACKGROUND,
)


def priority_for_task_type(task_type: str) -> int:
    if task_type in {"ASK_DOCUMENT", "EXPORT_MARKDOWN"}:
        return PRIORITY_INTERACTIVE
    if task_type in {"GENERATE_EMBEDDINGS", "MAINTAIN_CONVERSATION_MEMORY"}:
        return PRIORITY_BACKGROUND
    return PRIORITY_USER_VISIBLE


@dataclass(frozen=True)
class TaskPayload:
    task_id: str
    document_id: str
    user_id: str
    task_type: str
    priority: int | None = None
    enqueued_at: float | None = None
    conversation_id: str | None = None
    # Grading tasks target a specific quiz attempt rather than a document.
    attempt_id: str | None = None

    @property
    def resolved_priority(self) -> int:
        if self.priority in ALL_PRIORITIES:
            return int(self.priority)
        return priority_for_task_type(self.task_type)


class RedisTaskQueue:
    def __init__(self) -> None:
        self._client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._schedule_index = 0

    def queue_name(self, priority: int) -> str:
        return f"{settings.document_queue}:priority:{priority}"

    def pop(self, allowed_priorities: tuple[int, ...] = ALL_PRIORITIES) -> Optional[TaskPayload]:
        allowed = tuple(priority for priority in allowed_priorities if priority in ALL_PRIORITIES)
        if not allowed:
            return None

        # First use weighted non-blocking probes. This provides bounded service
        # to background work even while higher-priority lists remain non-empty.
        for _ in range(len(PRIORITY_SCHEDULE)):
            priority = PRIORITY_SCHEDULE[self._schedule_index % len(PRIORITY_SCHEDULE)]
            self._schedule_index += 1
            if priority not in allowed:
                continue
            raw_payload = self._client.lpop(self.queue_name(priority))
            if raw_payload is not None:
                return self._decode(raw_payload, priority)

        # Block only after every allowed queue has been probed. The legacy base
        # queue remains last for zero-downtime upgrades.
        queue_names = [self.queue_name(priority) for priority in allowed]
        queue_names.append(settings.document_queue)
        item = self._client.blpop(queue_names, timeout=settings.block_timeout_seconds)
        if item is None:
            return None
        queue_name, raw_payload = item
        priority = next(
            (value for value in allowed if queue_name == self.queue_name(value)),
            priority_for_task_type(json.loads(raw_payload).get("taskType", "")),
        )
        decoded = self._decode(raw_payload, priority)
        if queue_name == settings.document_queue and decoded.resolved_priority not in allowed:
            # Re-home legacy payloads instead of violating the admission limit.
            self.push(decoded)
            return None
        return decoded

    def push(self, payload: TaskPayload) -> None:
        priority = payload.resolved_priority
        raw_payload = json.dumps(
            {
                "taskId": payload.task_id,
                "documentId": payload.document_id,
                "userId": payload.user_id,
                "taskType": payload.task_type,
                "priority": priority,
                "enqueuedAt": payload.enqueued_at or time.time(),
                **({"conversationId": payload.conversation_id} if payload.conversation_id else {}),
                **({"attemptId": payload.attempt_id} if payload.attempt_id else {}),
            },
            separators=(",", ":"),
        )
        self._client.rpush(self.queue_name(priority), raw_payload)

    def _decode(self, raw_payload: str, queue_priority: int) -> TaskPayload:
        payload = json.loads(raw_payload)
        priority = payload.get("priority")
        return TaskPayload(
            task_id=payload["taskId"],
            document_id=payload["documentId"],
            user_id=payload["userId"],
            task_type=payload["taskType"],
            priority=int(priority) if priority in ALL_PRIORITIES else queue_priority,
            enqueued_at=float(payload["enqueuedAt"]) if payload.get("enqueuedAt") else None,
            conversation_id=payload.get("conversationId") or None,
            attempt_id=payload.get("attemptId") or None,
        )
