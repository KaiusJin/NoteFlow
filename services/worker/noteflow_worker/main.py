from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from noteflow_worker.config import settings
from noteflow_worker.db.repository import Repository
from noteflow_worker.pipelines.generate_embeddings import GenerateEmbeddingsPipeline
from noteflow_worker.pipelines.generate_notes import GenerateNotesPipeline
from noteflow_worker.pipelines.parse_document import ParseDocumentPipeline
from noteflow_worker.queue.redis_queue import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_USER_VISIBLE,
    RedisTaskQueue,
    TaskPayload,
)


def process_payload(payload: TaskPayload) -> None:
    repository = Repository()
    parse_pipeline = ParseDocumentPipeline(repository)
    embeddings_pipeline = GenerateEmbeddingsPipeline(repository)
    notes_pipeline = GenerateNotesPipeline(repository)
    if payload.task_type == "PARSE_DOCUMENT":
        print(f"Processing parse task {payload.task_id} for document {payload.document_id}")
        parse_pipeline.run(payload)
        return
    if payload.task_type == "GENERATE_EMBEDDINGS":
        print(f"Processing embeddings task {payload.task_id} for document {payload.document_id}")
        embeddings_pipeline.run(payload)
        return
    if payload.task_type == "GENERATE_NOTES":
        print(f"Processing notes task {payload.task_id} for document {payload.document_id}")
        notes_pipeline.run(payload)
        return
    print(f"Skipping unsupported task type: {payload.task_type}")


def main() -> None:
    queue = RedisTaskQueue()
    max_tasks = max(1, settings.worker_max_concurrent_tasks)
    background_limit = min(
        max(1, settings.worker_max_background_tasks),
        max_tasks if max_tasks == 1 else max_tasks - 1,
    )
    active: dict[Future, int] = {}
    print(
        "NoteFlow worker started. Waiting for document tasks... "
        f"max_concurrent_tasks={max_tasks} max_background_tasks={background_limit}"
    )
    recover_stale_notes_tasks(queue)
    recover_stale_parse_tasks(queue)

    with ThreadPoolExecutor(max_workers=max_tasks) as executor:
        while True:
            active = reap_completed(active)
            if len(active) >= max_tasks:
                done, _ = wait(set(active), return_when=FIRST_COMPLETED)
                log_completed(done)
                active = {future: priority for future, priority in active.items() if future not in done}
                continue

            background_active = sum(priority == PRIORITY_BACKGROUND for priority in active.values())
            allowed_priorities = (PRIORITY_INTERACTIVE, PRIORITY_USER_VISIBLE)
            if background_active < background_limit:
                allowed_priorities = (*allowed_priorities, PRIORITY_BACKGROUND)
            payload = queue.pop(allowed_priorities)
            if payload is None:
                continue
            active[executor.submit(process_payload, payload)] = payload.resolved_priority


def reap_completed(active: dict[Future, int]) -> dict[Future, int]:
    done = {future for future in active if future.done()}
    if done:
        log_completed(done)
    return {future: priority for future, priority in active.items() if future not in done}


def log_completed(done: set[Future]) -> None:
    for future in done:
        try:
            future.result()
        except Exception as exc:
            print(f"Task failed but worker will continue: {exc}")


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
        print(f"Recovered stale notes task {payload.task_id} for document {payload.document_id}")
    if rows:
        print(f"Recovered {len(rows)} stale notes task(s) on worker startup.")


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
        print(f"Recovered stale parse task {payload.task_id} for document {payload.document_id}")
    if rows:
        print(f"Recovered {len(rows)} stale parse task(s) on worker startup.")


if __name__ == "__main__":
    main()
