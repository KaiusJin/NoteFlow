from __future__ import annotations

import sys

from noteflow_worker.db.repository import Repository
from noteflow_worker.pdf.markdown import build_markdown_document


def main() -> int:
    document_ids = sys.argv[1:]
    if not document_ids:
        print("Usage: backfill_markdown.py <document_id> [<document_id> ...]")
        return 2

    repository = Repository()
    for document_id in document_ids:
        document = repository.load_document(document_id)
        blocks = repository.load_layout_blocks(document_id)
        vlm_results = repository.load_vlm_results(document_id)
        markdown = build_markdown_document(
            document_id,
            blocks,
            vlm_results,
            document_type=document.document_type,
        )
        repository.replace_markdown_pages(document_id, markdown.pages)
        repository.save_markdown_document(markdown.document)
        print(f"{document_id} pages={len(markdown.pages)} chars={len(markdown.document.markdown)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
