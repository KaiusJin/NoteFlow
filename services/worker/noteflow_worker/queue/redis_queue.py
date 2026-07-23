import json
import time
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

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
    if task_type in {"ASK_DOCUMENT", "EXPORT_MARKDOWN", "ANSWER_CONVERSATION_TURN", "RESUME_AGENT_RUN"}:
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
    # Answer-turn tasks target the assistant placeholder message.
    message_id: str | None = None
    # Redis-side delivery lease. New producers do not need to set this.
    lease_id: str | None = None

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

    @property
    def _lease_payloads_key(self) -> str:
        return f"{settings.document_queue}:processing:payloads"

    @property
    def _lease_deadlines_key(self) -> str:
        return f"{settings.document_queue}:processing:deadlines"

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
            raw_payload, lease_id = self._lease_from_queue(self.queue_name(priority))
            if raw_payload is not None:
                return self._decode(raw_payload, priority, lease_id)

        # Probe the legacy base queue last for zero-downtime upgrades. Keep this
        # on the same atomic lease path as priority queues; BLPOP would create a
        # crash window between removing the item and recording its lease.
        raw_payload, lease_id = self._lease_from_queue(settings.document_queue)
        if raw_payload is not None:
            priority = priority_for_task_type(json.loads(raw_payload).get("taskType", ""))
            decoded = self._decode(raw_payload, priority, lease_id)
            if decoded.resolved_priority in allowed:
                return decoded
            # Re-home legacy payloads instead of violating the admission limit.
            self.ack(decoded)
            self.push(decoded)
            return None

        time.sleep(max(0.1, min(1.0, settings.block_timeout_seconds)))
        return None

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
                **({"messageId": payload.message_id} if payload.message_id else {}),
            },
            separators=(",", ":"),
        )
        self._client.rpush(self.queue_name(priority), raw_payload)

    def ack(self, payload: TaskPayload) -> None:
        if not payload.lease_id:
            return
        pipe = self._client.pipeline()
        pipe.hdel(self._lease_payloads_key, payload.lease_id)
        pipe.zrem(self._lease_deadlines_key, payload.lease_id)
        pipe.execute()

    def extend_lease(self, payload: TaskPayload) -> None:
        if not payload.lease_id:
            return
        if not self._client.hexists(self._lease_payloads_key, payload.lease_id):
            return
        deadline = time.time() + max(30, settings.queue_lease_seconds)
        self._client.zadd(self._lease_deadlines_key, {payload.lease_id: deadline})

    def reclaim_expired_leases(self) -> int:
        now = time.time()
        limit = max(1, settings.queue_reclaim_batch_size)
        expired = self._client.zrangebyscore(
            self._lease_deadlines_key,
            min="-inf",
            max=now,
            start=0,
            num=limit,
        )
        reclaimed = 0
        for lease_id in expired:
            raw_payload = self._client.hget(self._lease_payloads_key, lease_id)
            pipe = self._client.pipeline()
            pipe.hdel(self._lease_payloads_key, lease_id)
            pipe.zrem(self._lease_deadlines_key, lease_id)
            pipe.execute()
            if raw_payload is None:
                continue
            self.push(self._decode(raw_payload, priority_for_task_type(json.loads(raw_payload).get("taskType", ""))))
            reclaimed += 1
        return reclaimed

    def _lease_from_queue(self, queue_name: str) -> tuple[str | None, str | None]:
        lease_id = str(uuid4())
        deadline = time.time() + max(30, settings.queue_lease_seconds)
        raw_payload = self._client.eval(
            """
            local payload = redis.call('LPOP', KEYS[1])
            if payload then
              redis.call('HSET', KEYS[2], ARGV[1], payload)
              redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])
            end
            return payload
            """,
            3,
            queue_name,
            self._lease_payloads_key,
            self._lease_deadlines_key,
            lease_id,
            deadline,
        )
        if raw_payload is None:
            return None, None
        return raw_payload, lease_id

    def _decode(self, raw_payload: str, queue_priority: int, lease_id: str | None = None) -> TaskPayload:
        payload = json.loads(raw_payload)
        priority = payload.get("priority")
        document_id = payload.get("documentId") or ""
        return TaskPayload(
            task_id=payload["taskId"],
            # Conversation turns are not document-scoped; tolerate absent ids.
            document_id="" if document_id in {"", "null", "None"} else document_id,
            user_id=payload["userId"],
            task_type=payload["taskType"],
            priority=int(priority) if priority in ALL_PRIORITIES else queue_priority,
            enqueued_at=float(payload["enqueuedAt"]) if payload.get("enqueuedAt") else None,
            conversation_id=payload.get("conversationId") or None,
            attempt_id=payload.get("attemptId") or None,
            message_id=payload.get("messageId") or None,
            lease_id=lease_id,
        )
