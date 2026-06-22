from noteflow_worker.db.repository import Repository
from noteflow_worker.pipelines.parse_document import ParseDocumentPipeline
from noteflow_worker.queue.redis_queue import RedisTaskQueue


def main() -> None:
    queue = RedisTaskQueue()
    repository = Repository()
    pipeline = ParseDocumentPipeline(repository)
    print("NoteFlow worker started. Waiting for document tasks...")

    while True:
        payload = queue.pop()
        if payload is None:
            continue
        if payload.task_type != "PARSE_DOCUMENT":
            print(f"Skipping unsupported task type: {payload.task_type}")
            continue
        print(f"Processing parse task {payload.task_id} for document {payload.document_id}")
        pipeline.run(payload)


if __name__ == "__main__":
    main()
