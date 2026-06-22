from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.db.repository import Repository
from noteflow_worker.pdf.layout import build_markdown_chunks


def load_page_asset_ids(repository: Repository, document_id: str) -> dict[int, str]:
    with repository.connect() as conn:
        rows = conn.execute(
            """
            SELECT page_number, id
            FROM document_page_assets
            WHERE document_id = %s
            ORDER BY page_number
            """,
            (document_id,),
        ).fetchall()
    return {row["page_number"]: str(row["id"]) for row in rows}


def main() -> int:
    repository = Repository()
    requested_document_ids = set(sys.argv[1:])

    with repository.connect() as conn:
        rows = conn.execute(
            """
            SELECT
              d.id,
              d.title,
              d.content_source_type,
              m.markdown
            FROM documents d
            JOIN document_markdown_documents m ON m.document_id = d.id
            ORDER BY d.created_at, d.title
            """
        ).fetchall()

    if requested_document_ids:
        rows = [row for row in rows if str(row["id"]) in requested_document_ids]

    print(f"Found {len(rows)} documents with Markdown to rebuild chunks.")
    for index, row in enumerate(rows, start=1):
        document_id = str(row["id"])
        title = row["title"] or document_id
        content_source_type = row["content_source_type"]
        layout_blocks = repository.load_layout_blocks(document_id)
        vlm_results = repository.load_vlm_results(document_id)
        asset_ids_by_page = load_page_asset_ids(repository, document_id)
        chunks = build_markdown_chunks(
            row["markdown"],
            layout_blocks,
            vlm_results,
            asset_ids_by_page,
            content_source_type,
        )
        repository.replace_chunks(document_id, chunks)
        token_counts = [chunk.token_count or 0 for chunk in chunks]
        tiny_count = sum(1 for count in token_counts if count < 80)
        cross_page_count = sum(1 for chunk in chunks if (chunk.page_start or chunk.page_number) != (chunk.page_end or chunk.page_number))
        avg_tokens = round(sum(token_counts) / len(token_counts), 1) if token_counts else 0
        print(
            f"[{index}/{len(rows)}] {title}: chunks={len(chunks)} "
            f"avg_tokens={avg_tokens} tiny={tiny_count} cross_page={cross_page_count}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
