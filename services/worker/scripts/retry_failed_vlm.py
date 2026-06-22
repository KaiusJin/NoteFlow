from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.db.repository import LayoutBlock, Repository
from noteflow_worker.pdf.layout import build_markdown_chunks
from noteflow_worker.pdf.markdown import build_markdown_document
from noteflow_worker.pdf.regions import analyze_regions_with_vlm


PAGE_LEVEL_SOURCE_TYPES = {"SCANNED_PDF", "HANDWRITTEN_SCAN"}


def load_document_info(repository: Repository, document_id: str) -> tuple[str, str]:
    with repository.connect() as conn:
        row = conn.execute(
            """
            SELECT title, content_source_type
            FROM documents
            WHERE id = %s
            """,
            (document_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {document_id}")
    return row["title"] or document_id, row["content_source_type"] or ""


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


def rebuild_page_level_layout_blocks(repository: Repository, document_id: str, asset_ids_by_page: dict[int, str]) -> list[LayoutBlock]:
    vlm_results = repository.load_vlm_results(document_id)
    blocks: list[LayoutBlock] = []
    for result in vlm_results:
        blocks.append(
            LayoutBlock(
                document_id=document_id,
                page_number=result.page_number,
                block_index=result.region_index,
                block_type="PARAGRAPH",
                content=result.transcription or result.description or "",
                bbox_json=None,
                source_asset_id=asset_ids_by_page.get(result.page_number),
                confidence=0.85 if not result.error_message else 0.0,
                metadata_json=json.dumps({"source": "gemini_page_level_vlm"}, separators=(",", ":")),
            )
        )
    repository.replace_layout_blocks(document_id, blocks)
    return blocks


def rebuild_markdown_and_chunks(repository: Repository, document_id: str, content_source_type: str) -> None:
    asset_ids_by_page = load_page_asset_ids(repository, document_id)
    if content_source_type in PAGE_LEVEL_SOURCE_TYPES:
        layout_blocks = rebuild_page_level_layout_blocks(repository, document_id, asset_ids_by_page)
    else:
        layout_blocks = repository.load_layout_blocks(document_id)

    vlm_results = repository.load_vlm_results(document_id)
    markdown = build_markdown_document(document_id, layout_blocks, vlm_results)
    repository.replace_markdown_pages(document_id, markdown.pages)
    repository.save_markdown_document(markdown.document)
    chunks = build_markdown_chunks(
        markdown.document.markdown,
        layout_blocks,
        vlm_results,
        asset_ids_by_page,
        content_source_type,
    )
    repository.replace_chunks(document_id, chunks)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: retry_failed_vlm.py <document_id> [page_number ...]")
        return 2

    document_id = sys.argv[1]
    requested_pages = {int(value) for value in sys.argv[2:]}
    repository = Repository()
    title, content_source_type = load_document_info(repository, document_id)

    regions = repository.load_visual_regions(document_id)
    vlm_results = repository.load_vlm_results(document_id)
    failed_keys = {
        (result.page_number, result.region_index)
        for result in vlm_results
        if result.error_message
    }
    target_regions = [
        region
        for region in regions
        if (requested_pages and region.page_number in requested_pages)
        or (not requested_pages and (region.page_number, region.region_index) in failed_keys)
    ]
    if not target_regions:
        print(f"No target VLM regions found for {title}.")
        return 0

    print(f"Retrying {len(target_regions)} VLM region(s) for {title}...")
    retry_results = analyze_regions_with_vlm(target_regions, fail_on_error=True)
    retry_by_key = {
        (result.page_number, result.region_index, result.provider, result.model): result
        for result in retry_results
    }
    merged = []
    replaced_keys = set()
    for result in vlm_results:
        key = (result.page_number, result.region_index, result.provider, result.model)
        if key in retry_by_key:
            merged.append(retry_by_key[key])
            replaced_keys.add(key)
        else:
            merged.append(result)
    for key, result in retry_by_key.items():
        if key not in replaced_keys:
            merged.append(result)

    repository.replace_vlm_results(document_id, merged)
    rebuild_markdown_and_chunks(repository, document_id, content_source_type)
    print(f"Rebuilt Markdown and chunks for {title}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
