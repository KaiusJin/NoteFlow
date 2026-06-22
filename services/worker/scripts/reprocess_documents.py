from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.db.repository import Repository
from noteflow_worker.pipelines.parse_document import ParseDocumentPipeline
from noteflow_worker.queue.redis_queue import TaskPayload


def main() -> int:
    document_ids = sys.argv[1:]
    if not document_ids:
        print("Usage: reprocess_documents.py <document_id> [<document_id> ...]")
        return 2

    repository = Repository()
    pipeline = ParseDocumentPipeline(repository)
    for index, document_id in enumerate(document_ids, start=1):
        with repository.connect() as conn:
            document = conn.execute(
                "SELECT title FROM documents WHERE id = %s",
                (document_id,),
            ).fetchone()
            if document is None:
                raise ValueError(f"Document not found: {document_id}")
            task_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO tasks (id, status, progress, retry_count, document_id, created_at, updated_at)
                VALUES (%s, 'PENDING', 0, 0, %s, NOW(), NOW())
                """,
                (task_id, document_id),
            )
            conn.execute("UPDATE documents SET status = 'PROCESSING' WHERE id = %s", (document_id,))

        title = document["title"] or document_id
        print(f"[{index}/{len(document_ids)}] Reprocessing {title} ({document_id})...")
        pipeline.run(
            TaskPayload(
                task_id=task_id,
                document_id=document_id,
                user_id=str(uuid.uuid4()),
                task_type="PARSE_DOCUMENT",
            )
        )
        print(f"Finished {title}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
