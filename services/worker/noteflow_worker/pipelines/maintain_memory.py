from __future__ import annotations

import json

from noteflow_worker.memory.manager import ConversationMemoryManager
from noteflow_worker.memory.store import MemoryStore
from noteflow_worker.queue.redis_queue import TaskPayload


class MaintainConversationMemoryPipeline:
    """Background task: summary compression + long-term memory extraction.

    Progress is deliberately coarse: maintenance is short compared to parsing
    and there is no user waiting on intermediate steps. Failures re-raise so
    the worker's stale-task recovery can re-enqueue; the manager's watermarks
    make every retry incremental instead of repeating completed work.
    """

    def __init__(self, store: MemoryStore | None = None, manager: ConversationMemoryManager | None = None) -> None:
        self._store = store or MemoryStore()
        self._manager = manager or ConversationMemoryManager(store=self._store)

    def run(self, payload: TaskPayload) -> None:
        if not payload.conversation_id:
            self._store.mark_task_failed(payload.task_id, "MAINTAIN_CONVERSATION_MEMORY requires a conversationId.")
            raise ValueError("MAINTAIN_CONVERSATION_MEMORY payload is missing conversationId.")
        try:
            self._store.mark_task_processing(payload.task_id, "MAINTAINING_MEMORY", 10)
            self._store.ensure_memory_schema()
            report = self._manager.run_maintenance(payload.conversation_id)
            print(
                "Conversation memory maintenance "
                + json.dumps(
                    {
                        "conversationId": report.conversation_id,
                        "summarized": report.summarized,
                        "summaryVersion": report.summary_version,
                        "evictedMessages": report.evicted_message_count,
                        "extractionRan": report.extraction_ran,
                        "candidates": report.candidates_extracted,
                        "added": report.memories_added,
                        "updated": report.memories_updated,
                        "skipped": report.memories_skipped,
                        "errors": report.errors,
                    },
                    separators=(",", ":"),
                )
            )
            hard_errors = [error for error in report.errors if error != "maintenance_already_running"]
            if hard_errors:
                raise RuntimeError("; ".join(hard_errors))
            self._store.mark_task_completed(payload.task_id)
        except Exception as exc:
            self._store.mark_task_failed(payload.task_id, str(exc))
            raise
