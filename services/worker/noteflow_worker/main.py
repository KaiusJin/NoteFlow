import multiprocessing
import logging
from concurrent.futures import (
    FIRST_COMPLETED,
    Executor,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)

from noteflow_worker.config import settings
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
from noteflow_worker.observability import initialize_observability, task_span

logger = logging.getLogger("noteflow.worker")

def process_payload(payload: TaskPayload) -> None:
    with task_span(payload.task_type, payload.task_id, payload.event_id):
        _process_payload(payload)


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


def main() -> None:
    initialize_observability()
    queue = RedisTaskQueue()
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
    recover_stale_notes_tasks(queue)
    recover_stale_study_tasks(queue)
    recover_stale_answer_tasks(queue)
    recover_stale_parse_tasks(queue)

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
    try:
        with ThreadPoolExecutor(max_workers=max_tasks) as executor:
            while True:
                reclaimed = queue.reclaim_expired_leases()
                if reclaimed:
                    logger.warning("redis_leases_reclaimed count=%s", reclaimed)
                refresh_active_leases(queue, active)
                active = reap_completed(queue, active)
                if len(active) >= total_capacity:
                    done, _ = wait(set(active), timeout=lease_wait_timeout_seconds(), return_when=FIRST_COMPLETED)
                    finish_completed(queue, done, active)
                    active = {future: payload for future, payload in active.items() if future not in done}
                    continue

                background_active = sum(payload.resolved_priority == PRIORITY_BACKGROUND for payload in active.values())
                allowed_priorities = (PRIORITY_INTERACTIVE, PRIORITY_USER_VISIBLE)
                if background_active < background_limit:
                    allowed_priorities = (*allowed_priorities, PRIORITY_BACKGROUND)
                payload = queue.pop(allowed_priorities)
                if payload is None:
                    continue
                target: Executor = executor
                if parse_executor is not None and payload.task_type == "PARSE_DOCUMENT":
                    target = parse_executor
                active[target.submit(process_payload, payload)] = payload
    finally:
        if parse_executor is not None:
            parse_executor.shutdown(wait=False, cancel_futures=True)


def lease_wait_timeout_seconds() -> float:
    return max(5.0, min(30.0, settings.queue_lease_seconds / 3))


def refresh_active_leases(queue: RedisTaskQueue, active: dict[Future, TaskPayload]) -> None:
    for future, payload in active.items():
        if not future.done():
            queue.extend_lease(payload)


def reap_completed(queue: RedisTaskQueue, active: dict[Future, TaskPayload]) -> dict[Future, TaskPayload]:
    done = {future for future in active if future.done()}
    if done:
        finish_completed(queue, done, active)
    return {future: payload for future, payload in active.items() if future not in done}


def finish_completed(queue: RedisTaskQueue, done: set[Future], active: dict[Future, TaskPayload]) -> None:
    for future in done:
        payload = active.get(future)
        try:
            future.result()
        except Exception as exc:
            logger.exception("task_failed worker_continues=true error=%s", exc)
        finally:
            if payload is not None:
                schedule_agent_resumes(queue, payload.task_id)
                queue.ack(payload)


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


def recover_stale_notes_tasks(queue: RedisTaskQueue) -> None:
    repository = Repository()
    rows = repository.recover_stale_generate_notes_tasks(settings.notes_stale_task_after_minutes)
    for row in rows:
        payload = TaskPayload(
            task_id=str(row["id"]),
            document_id=str(row["document_id"]),
            user_id=str(row["user_id"]),
            task_type=row["task_type"],
        )
        queue.push(payload)
        logger.warning("stale_task_recovered task_type=GENERATE_NOTES task_id=%s document_id=%s", payload.task_id, payload.document_id)
    if rows:
        logger.warning("stale_tasks_recovered task_type=GENERATE_NOTES count=%s", len(rows))


def recover_stale_parse_tasks(queue: RedisTaskQueue) -> None:
    repository = Repository()
    rows = repository.recover_stale_parse_tasks(
        settings.parse_stale_task_after_minutes,
        settings.parse_max_task_retries,
    )
    for row in rows:
        payload = TaskPayload(
            task_id=str(row["id"]),
            document_id=str(row["document_id"]),
            user_id=str(row["user_id"]),
            task_type=row["task_type"],
        )
        queue.push(payload)
        logger.warning("stale_task_recovered task_type=PARSE_DOCUMENT task_id=%s document_id=%s", payload.task_id, payload.document_id)
    if rows:
        logger.warning("stale_tasks_recovered task_type=PARSE_DOCUMENT count=%s", len(rows))


def recover_stale_study_tasks(queue: RedisTaskQueue) -> None:
    repository = StudyRepository()
    repository.ensure_study_schema()
    rows = repository.recover_stale_study_tasks(settings.study_stale_task_after_minutes)
    for row in rows:
        payload = TaskPayload(task_id=str(row["id"]), document_id=str(row["document_id"]),
            user_id=str(row["user_id"]), task_type=row["task_type"],
            attempt_id=str(row["attempt_id"]) if row.get("attempt_id") else None)
        queue.push(payload)
        logger.warning("stale_task_recovered task_type=%s task_id=%s", payload.task_type, payload.task_id)


def recover_stale_answer_tasks(queue: RedisTaskQueue) -> None:
    store = ConversationStore()
    store.ensure_conversation_schema()
    rows = store.recover_stale_answer_tasks(settings.answer_stale_task_after_minutes)
    for row in rows:
        if not row.get("conversation_id") or not row.get("message_id"):
            store.mark_task_failed(str(row["id"]), "Recovered answer task is missing its target mapping.")
            continue
        payload = TaskPayload(
            task_id=str(row["id"]), document_id="", user_id=str(row["user_id"]),
            task_type=row["task_type"], conversation_id=str(row["conversation_id"]),
            message_id=str(row["message_id"]),
        )
        queue.push(payload)
        logger.warning("stale_task_recovered task_type=%s task_id=%s conversation_id=%s", payload.task_type, payload.task_id, payload.conversation_id)


if __name__ == "__main__":
    main()
