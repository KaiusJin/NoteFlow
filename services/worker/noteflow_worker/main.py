import multiprocessing
import logging
import signal
import threading
import time
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import replace

from noteflow_worker.config import clear_task_ai_overrides, settings
from noteflow_worker.db.repository import Repository
from noteflow_worker.pipelines.generate_embeddings import GenerateEmbeddingsPipeline
from noteflow_worker.pipelines.generate_notes import GenerateNotesPipeline
from noteflow_worker.pipelines.generate_flashcards import GenerateFlashcardsPipeline
from noteflow_worker.pipelines.generate_quiz import GenerateQuizPipeline
from noteflow_worker.pipelines.grade_quiz_attempt import GradeQuizAttemptPipeline
from noteflow_worker.pipelines.answer_conversation_turn import AnswerConversationTurnPipeline
from noteflow_worker.pipelines.maintain_memory import MaintainConversationMemoryPipeline
from noteflow_worker.pipelines.parse_document import ParseDocumentPipeline
from noteflow_worker.queue.redis_queue import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_USER_VISIBLE,
    RedisTaskQueue,
    TaskPayload,
)
from noteflow_worker.study.repository import StudyRepository
from noteflow_worker.conversation.store import ConversationStore
from noteflow_worker.user_settings import apply_user_ai_settings
from noteflow_worker.runtime.sandbox import initialize_parse_worker_sandbox
from noteflow_worker.runtime.execution_context import task_execution_scope
from noteflow_worker.observability import initialize_observability, task_span
from noteflow_worker.task_execution import TERMINAL_STATUSES, TaskExecutionStore

logger = logging.getLogger("noteflow.worker")

# Only these task types can be spawned asynchronously by the agent and therefore
# carry agent-run resume rows; every other completion skips the resume lookup.
AGENT_RESUMABLE_TASK_TYPES = {
    "GENERATE_NOTES",
    "GENERATE_FLASHCARDS",
    "GENERATE_QUIZ",
    "GRADE_QUIZ_ATTEMPT",
}


def process_payload(payload: TaskPayload) -> None:
    try:
        with task_execution_scope(payload.lease_id, payload.user_id):
            with task_span(payload.task_type, payload.task_id, payload.event_id):
                _process_payload(payload)
    finally:
        # Pooled threads are reused across users; never let one task's AI
        # settings snapshot leak into the next task on the same thread.
        clear_task_ai_overrides()


def _process_payload(payload: TaskPayload) -> None:
    apply_user_ai_settings(payload.user_id)
    repository = Repository()
    parse_pipeline = ParseDocumentPipeline(repository)
    embeddings_pipeline = GenerateEmbeddingsPipeline(repository)
    notes_pipeline = GenerateNotesPipeline(repository)
    if payload.task_type == "PARSE_DOCUMENT":
        logger.info("processing_task task_type=PARSE_DOCUMENT task_id=%s document_id=%s trace_id=%s", payload.task_id, payload.document_id, payload.event_id)
        parse_pipeline.run(payload)
        return
    if payload.task_type == "GENERATE_EMBEDDINGS":
        logger.info("processing_task task_type=GENERATE_EMBEDDINGS task_id=%s document_id=%s trace_id=%s", payload.task_id, payload.document_id, payload.event_id)
        embeddings_pipeline.run(payload)
        return
    if payload.task_type == "GENERATE_NOTES":
        logger.info("processing_task task_type=GENERATE_NOTES task_id=%s document_id=%s trace_id=%s", payload.task_id, payload.document_id, payload.event_id)
        notes_pipeline.run(payload)
        return
    if payload.task_type == "GENERATE_FLASHCARDS":
        logger.info("processing_task task_type=GENERATE_FLASHCARDS task_id=%s document_id=%s trace_id=%s", payload.task_id, payload.document_id, payload.event_id)
        GenerateFlashcardsPipeline(StudyRepository()).run(payload)
        return
    if payload.task_type == "GENERATE_QUIZ":
        logger.info("processing_task task_type=GENERATE_QUIZ task_id=%s document_id=%s trace_id=%s", payload.task_id, payload.document_id, payload.event_id)
        GenerateQuizPipeline(StudyRepository()).run(payload)
        return
    if payload.task_type == "GRADE_QUIZ_ATTEMPT":
        logger.info("processing_task task_type=GRADE_QUIZ_ATTEMPT task_id=%s attempt_id=%s trace_id=%s", payload.task_id, payload.attempt_id, payload.event_id)
        GradeQuizAttemptPipeline(StudyRepository()).run(payload)
        return
    if payload.task_type in {"ANSWER_CONVERSATION_TURN", "RESUME_AGENT_RUN"}:
        logger.info("processing_task task_type=%s task_id=%s conversation_id=%s message_id=%s trace_id=%s", payload.task_type, payload.task_id, payload.conversation_id, payload.message_id, payload.event_id)
        AnswerConversationTurnPipeline().run(payload)
        return
    if payload.task_type == "MAINTAIN_CONVERSATION_MEMORY":
        logger.info("processing_task task_type=MAINTAIN_CONVERSATION_MEMORY task_id=%s conversation_id=%s trace_id=%s", payload.task_id, payload.conversation_id, payload.event_id)
        MaintainConversationMemoryPipeline().run(payload)
        return
    logger.warning("unsupported_task task_type=%s task_id=%s", payload.task_type, payload.task_id)
    repository.mark_task_failed(payload.task_id, f"Unsupported task type: {payload.task_type}")
    raise ValueError(f"Unsupported task type: {payload.task_type}")


def main() -> None:
    initialize_observability()
    queue = RedisTaskQueue()
    executions = TaskExecutionStore()
    stop_event = threading.Event()

    def request_stop(signum, frame) -> None:
        logger.warning("shutdown_requested signal=%s in_flight=%d", signum, len(active))
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    max_tasks = max(1, settings.worker_max_concurrent_tasks)
    background_limit = min(
        max(1, settings.worker_max_background_tasks),
        max_tasks if max_tasks == 1 else max_tasks - 1,
    )
    parse_workers = max(0, settings.worker_parse_process_workers)
    active: dict[Future, TaskPayload] = {}
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s level=%(levelname)s logger=%(name)s message=%(message)s",
    )
    logger.info(
        "NoteFlow worker started. Waiting for document tasks... "
        f"max_concurrent_tasks={max_tasks} max_background_tasks={background_limit} "
        f"parse_process_workers={parse_workers}"
    )
    run_stale_task_recovery(queue, executions)

    # CPU-bound parsing runs in spawned processes so it cannot starve the GIL
    # for the I/O-bound pipelines in the thread pool. spawn (never fork) keeps
    # the child from inheriting DB pool sockets and Redis connections; each
    # child lazily builds its own connection pool.
    parse_executor: ProcessPoolExecutor | None = None
    if parse_workers > 0:
        parse_executor = ProcessPoolExecutor(
            max_workers=parse_workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=initialize_parse_worker_sandbox,
        )
    total_capacity = max_tasks + (parse_workers if parse_executor else 0)
    last_recovery = time.monotonic()
    last_lease_extend = 0.0
    last_redis_reclaim = 0.0
    claimed_count = 0
    executor = ThreadPoolExecutor(max_workers=max_tasks)
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            if now - last_lease_extend >= lease_extend_interval_seconds():
                refresh_active_leases(queue, executions, active)
                last_lease_extend = now
            if settings.recovery_interval_seconds > 0 and now - last_recovery >= settings.recovery_interval_seconds:
                run_stale_task_recovery(queue, executions)
                last_recovery = now
            if now - last_redis_reclaim >= max(5.0, settings.queue_reclaim_interval_seconds):
                reclaimed = queue.reclaim_expired_leases()
                if reclaimed:
                    logger.warning("redis_leases_reclaimed count=%s", reclaimed)
                last_redis_reclaim = now

            active = reap_completed(queue, executions, active)
            if len(active) >= total_capacity:
                done, _ = wait(set(active), timeout=lease_wait_timeout_seconds(), return_when=FIRST_COMPLETED)
                finish_completed(queue, executions, done, active)
                active = {future: payload for future, payload in active.items() if future not in done}
                continue

            max_per_run = max(0, settings.worker_max_tasks_per_run)
            if max_per_run and claimed_count >= max_per_run:
                stop_event.set()
                continue

            background_active = sum(payload.resolved_priority == PRIORITY_BACKGROUND for payload in active.values())
            allowed_priorities = (PRIORITY_INTERACTIVE, PRIORITY_USER_VISIBLE)
            if background_active < background_limit:
                allowed_priorities = (*allowed_priorities, PRIORITY_BACKGROUND)
            payload = queue.pop(allowed_priorities)
            if payload is None:
                # Demand-driven Cloud Run Jobs exit after one empty probe once
                # all admitted work has drained. Local workers keep polling.
                if max_per_run and not active:
                    stop_event.set()
                continue
            if not claim_delivery(queue, executions, payload):
                continue
            claimed_count += 1
            target: Executor = executor
            if parse_executor is not None and payload.task_type == "PARSE_DOCUMENT":
                target = parse_executor
            active[target.submit(process_payload, payload)] = payload
    finally:
        active = drain_in_flight(queue, executions, active)
        if active:
            logger.warning("shutdown_abandoned_tasks count=%d", len(active))
        executor.shutdown(wait=False, cancel_futures=True)
        if parse_executor is not None:
            parse_executor.shutdown(wait=False, cancel_futures=True)


def lease_wait_timeout_seconds() -> float:
    return max(5.0, min(30.0, settings.queue_lease_seconds / 3))


def lease_extend_interval_seconds() -> float:
    # Extending a lease every poll iteration is pure overhead for long leases;
    # a third of the lease period keeps the deadline comfortably ahead.
    return max(30.0, settings.queue_lease_seconds / 3)


def run_stale_task_recovery(queue: RedisTaskQueue, executions: TaskExecutionStore) -> None:
    try:
        recovered = executions.recover_expired(
            settings.task_execution_stale_without_lease_minutes,
            settings.queue_max_redeliveries,
            settings.queue_reclaim_batch_size,
        )
        retry_rows = executions.ready_retries(settings.queue_reclaim_batch_size)
        for row in recovered:
            if row["status"] == "FAILED":
                failed_payload = task_payload_from_row(row)
                queue.dead_letter(failed_payload, "PostgreSQL execution lease retry budget exhausted")
                if failed_payload.task_type in AGENT_RESUMABLE_TASK_TYPES:
                    schedule_agent_resumes(queue, failed_payload.task_id)
        for row in retry_rows:
            queue.push(task_payload_from_row(row))
        if recovered or retry_rows:
            logger.warning(
                "task_execution_recovery recovered=%d retry_wakeups=%d",
                len(recovered), len(retry_rows),
            )
    except Exception as exc:
        logger.exception("stale_task_recovery_failed error=%s", exc)


def refresh_active_leases(
    queue: RedisTaskQueue,
    executions: TaskExecutionStore,
    active: dict[Future, TaskPayload],
) -> None:
    for future, payload in active.items():
        if not future.done():
            queue.extend_lease(payload)
            if not executions.renew(payload, settings.queue_lease_seconds):
                logger.error("database_lease_renewal_failed task_id=%s lease_id=%s", payload.task_id, payload.lease_id)


def reap_completed(
    queue: RedisTaskQueue,
    executions: TaskExecutionStore,
    active: dict[Future, TaskPayload],
) -> dict[Future, TaskPayload]:
    done = {future for future in active if future.done()}
    if done:
        finish_completed(queue, executions, done, active)
    return {future: payload for future, payload in active.items() if future not in done}


def finish_completed(
    queue: RedisTaskQueue,
    executions: TaskExecutionStore,
    done: set[Future],
    active: dict[Future, TaskPayload],
) -> None:
    for future in done:
        payload = active.get(future)
        failure: Exception | None = None
        try:
            future.result()
        except Exception as exc:
            failure = exc
            logger.exception("task_failed worker_continues=true task_id=%s error=%s",
                             payload.task_id if payload else None, exc)
        if payload is None:
            continue

        state = executions.state(payload.task_id)
        if state is not None and state.terminal:
            queue.ack(payload)
            if payload.task_type in AGENT_RESUMABLE_TASK_TYPES:
                schedule_agent_resumes(queue, payload.task_id)
            continue

        reason = (
            f"{failure.__class__.__name__}: {failure}" if failure
            else "Task handler returned without recording a terminal state"
        )
        requeue_or_dead_letter(queue, executions, payload, reason)


def requeue_or_dead_letter(
    queue: RedisTaskQueue,
    executions: TaskExecutionStore,
    payload: TaskPayload,
    reason: str,
) -> None:
    """Settle an unexpected failure using PostgreSQL's retry counter."""
    state = executions.state(payload.task_id)
    if state and state.execution_id not in {None, payload.lease_id}:
        queue.ack(payload)
        return
    next_state = executions.retry_or_fail(
        payload,
        reason,
        max(0, settings.queue_max_redeliveries),
    )
    if next_state is None:
        queue.dead_letter(payload, "Task disappeared before its failure could be recorded")
        return
    if next_state.status == "FAILED":
        queue.dead_letter(payload, f"database retry budget exhausted: {reason}")
        if payload.task_type in AGENT_RESUMABLE_TASK_TYPES:
            schedule_agent_resumes(queue, payload.task_id)
        return
    if next_state.status != "RETRYING":
        # A compare-and-set can lose to another worker, cancellation, or a
        # terminal write. The old Redis delivery must not create fresh work.
        queue.ack(payload)
        logger.info(
            "stale_delivery_settled task_id=%s status=%s execution_id=%s",
            payload.task_id,
            next_state.status,
            next_state.execution_id,
        )
        if next_state.terminal and payload.task_type in AGENT_RESUMABLE_TASK_TYPES:
            schedule_agent_resumes(queue, payload.task_id)
        return
    retry = replace(payload, delivery_attempt=next_state.retry_count, lease_id=None)
    try:
        queue.push(retry)
        queue.ack(payload)
        logger.warning(
            "task_requeued task_id=%s delivery_attempt=%d error=%s",
            payload.task_id, next_state.retry_count, reason,
        )
    except Exception:
        logger.exception("task_requeue_failed task_id=%s", payload.task_id)


def claim_delivery(queue: RedisTaskQueue, executions: TaskExecutionStore, payload: TaskPayload) -> bool:
    if executions.claim(payload, settings.queue_lease_seconds):
        return True
    state = executions.state(payload.task_id)
    if state is None:
        queue.dead_letter(payload, "Task does not exist in PostgreSQL")
    else:
        queue.ack(payload)
        if state.status in TERMINAL_STATUSES and payload.task_type in AGENT_RESUMABLE_TASK_TYPES:
            schedule_agent_resumes(queue, payload.task_id)
    return False


def drain_in_flight(
    queue: RedisTaskQueue,
    executions: TaskExecutionStore,
    active: dict[Future, TaskPayload],
) -> dict[Future, TaskPayload]:
    drain_seconds = max(0.0, float(settings.worker_shutdown_drain_timeout_seconds))
    if active and drain_seconds > 0:
        logger.info("draining_in_flight_tasks count=%d timeout=%.0fs", len(active), drain_seconds)
    deadline = time.monotonic() + drain_seconds
    while active and time.monotonic() < deadline:
        refresh_active_leases(queue, executions, active)
        remaining = max(0.0, deadline - time.monotonic())
        done, _ = wait(set(active), timeout=min(5.0, remaining), return_when=FIRST_COMPLETED)
        if done:
            finish_completed(queue, executions, done, active)
            active = {future: payload for future, payload in active.items() if future not in done}
    return active


def task_payload_from_row(row: dict) -> TaskPayload:
    return TaskPayload(
        task_id=str(row["id"]),
        document_id=str(row.get("document_id") or ""),
        user_id=str(row["user_id"]),
        task_type=str(row["task_type"]),
        delivery_attempt=int(row.get("retry_count") or 0),
        attempt_id=str(row["attempt_id"]) if row.get("attempt_id") else None,
        conversation_id=str(row["conversation_id"]) if row.get("conversation_id") else None,
        message_id=str(row["message_id"]) if row.get("message_id") else None,
    )


def schedule_agent_resumes(queue: RedisTaskQueue, completed_task_id: str) -> None:
    try:
        for row in ConversationStore().create_resume_tasks(completed_task_id):
            queue.push(TaskPayload(
                task_id=row["task_id"], document_id="", user_id=row["user_id"],
                task_type="RESUME_AGENT_RUN", priority=PRIORITY_INTERACTIVE,
                conversation_id=row["conversation_id"], message_id=row["message_id"],
            ))
            logger.info("agent_resume_scheduled message_id=%s completed_task_id=%s", row["message_id"], completed_task_id)
    except Exception as exc:
        logger.exception("agent_resume_schedule_failed completed_task_id=%s error=%s", completed_task_id, exc)


if __name__ == "__main__":
    main()
