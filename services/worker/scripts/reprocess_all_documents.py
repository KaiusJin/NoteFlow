import sys
import uuid
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.db.repository import Repository
from noteflow_worker.pipelines.parse_document import ParseDocumentPipeline
from noteflow_worker.queue.redis_queue import TaskPayload

def main():
    repository = Repository()
    pipeline = ParseDocumentPipeline(repository)
    
    # Exclude already processed documents using the new code
    already_done = {
        "bf74186d-8467-4aa7-9c34-ac48274b8928", # STAT230Jun17
        "ba90d200-d47c-4b63-9ae6-87f2b81ba36e", # MATH138L25
    }
    
    with repository.connect() as conn:
        docs = conn.execute(
            "SELECT id, title, content_source_type FROM documents"
        ).fetchall()
        
    to_reprocess = [d for d in docs if str(d["id"]) not in already_done]
    print(f"Found {len(to_reprocess)} documents to re-process.")
    
    for idx, doc in enumerate(to_reprocess, start=1):
        doc_id = str(doc["id"])
        title = doc["title"]
        print(f"\n[{idx}/{len(to_reprocess)}] Reprocessing '{title}' ({doc_id}) [{doc['content_source_type']}]...")
        
        # Create a mock task in the database for tracking
        task_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        
        with repository.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, status, progress, retry_count, document_id, created_at, updated_at)
                VALUES (%s, 'PENDING', 0, 0, %s, NOW(), NOW())
                """,
                (task_id, doc_id)
            )
            # Mark document processing
            conn.execute("UPDATE documents SET status = 'PROCESSING' WHERE id = %s", (doc_id,))
            
        payload = TaskPayload(
            task_id=task_id,
            document_id=doc_id,
            user_id=user_id,
            task_type="PARSE_DOCUMENT"
        )
        
        try:
            pipeline.run(payload)
            print(f"Successfully reprocessed '{title}'!")
        except Exception as e:
            print(f"Error reprocessing '{title}': {e}")
            
    print("\nAll documents re-processing finished!")

if __name__ == "__main__":
    main()
