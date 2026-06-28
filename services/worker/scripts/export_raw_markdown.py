import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.db.repository import Repository

def main():
    repository = Repository()
    
    # Define target folder path
    export_dir = Path("/Users/kaius/Project/NoteFlow/exported_raw_markdown_2026-06-28_015400")
    export_dir.mkdir(parents=True, exist_ok=True)
    print(f"Export directory: {export_dir}")
    
    with repository.connect() as conn:
        # Load all documents ordered by title
        docs = conn.execute(
            "SELECT id, title FROM documents ORDER BY title"
        ).fetchall()
        
        print(f"Found {len(docs)} documents in db.")
        
        for idx, doc in enumerate(docs, start=1):
            doc_id = doc["id"]
            title = doc["title"]
            
            # Fetch raw markdown
            row = conn.execute(
                "SELECT markdown FROM document_markdown_documents WHERE document_id = %s",
                (doc_id,)
            ).fetchone()
            
            if not row or not row["markdown"]:
                print(f"[{idx:02d}] No markdown found for '{title}' ({doc_id})")
                continue
                
            markdown_content = row["markdown"]
            
            # Sanitize filename
            safe_title = title.replace("/", "_").replace("\\", "_")
            filename = f"{idx:02d} - {safe_title}.md"
            filepath = export_dir / filename
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(markdown_content)
                
            print(f"[{idx:02d}] Exported '{title}' to {filename} ({len(markdown_content)} bytes)")
            
    print("\nExport completed successfully!")

if __name__ == "__main__":
    main()
