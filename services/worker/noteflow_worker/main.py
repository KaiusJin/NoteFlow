from noteflow_worker.db.repository import Repository
from noteflow_worker.pipelines.generate_notes import GenerateNotesPipeline
from noteflow_worker.pipelines.parse_document import ParseDocumentPipeline
from noteflow_worker.queue.redis_queue import RedisTaskQueue, TaskPayload


def process_payload(payload: TaskPayload, parse_pipeline: ParseDocumentPipeline, notes_pipeline: GenerateNotesPipeline) -> None:
    if payload.task_type == "PARSE_DOCUMENT":
        print(f"Processing parse task {payload.task_id} for document {payload.document_id}")
        parse_pipeline.run(payload)
        return
    if payload.task_type == "GENERATE_NOTES":
        print(f"Processing notes task {payload.task_id} for document {payload.document_id}")
        notes_pipeline.run(payload)
        return
    print(f"Skipping unsupported task type: {payload.task_type}")


def main() -> None:
    queue = RedisTaskQueue()
    repository = Repository()
    parse_pipeline = ParseDocumentPipeline(repository)
    notes_pipeline = GenerateNotesPipeline(repository)
    print("NoteFlow worker started. Waiting for document tasks...")

    while True:
        payload = queue.pop()
        if payload is None:
            continue
        try:
            process_payload(payload, parse_pipeline, notes_pipeline)
        except Exception as exc:
            print(f"Task {payload.task_id} failed but worker will continue: {exc}")
            continue


if __name__ == "__main__":
    main()
