from noteflow_worker.db.repository import Repository
from noteflow_worker.pdf.parser import ensure_pdf_exists, parse_pdf
from noteflow_worker.pdf.layout import build_layout_parse, build_markdown_chunks
from noteflow_worker.pdf.markdown import build_markdown_document
from noteflow_worker.pdf.regions import analyze_regions_with_vlm, build_visual_regions, select_regions_for_vlm
from noteflow_worker.pdf.strategies import resolve_processing_strategy
from noteflow_worker.pdf.visual import analyze_pdf_visuals, to_page_assets
from noteflow_worker.queue.redis_queue import TaskPayload


class ParseDocumentPipeline:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def run(self, payload: TaskPayload) -> None:
        try:
            self._repository.mark_processing(payload.task_id, payload.document_id, "PARSING_PDF", 10)
            document = self._repository.load_document(payload.document_id)

            ensure_pdf_exists(document.storage_path)
            self._repository.mark_processing(payload.task_id, payload.document_id, "EXTRACTING_TEXT", 35)
            parsed = parse_pdf(document.storage_path, document.document_type)
            strategy = resolve_processing_strategy(document.document_type, parsed.content_source_type)

            if strategy.force_full_page_vlm:
                import json
                from noteflow_worker.db.repository import VisualRegion, LayoutBlock

                self._repository.mark_processing(payload.task_id, payload.document_id, "ANALYZING_VISUAL_CONTENT", 55)
                visual_pages = analyze_pdf_visuals(document.storage_path, payload.document_id)
                asset_ids_by_page = self._repository.replace_page_assets(
                    payload.document_id,
                    to_page_assets(payload.document_id, visual_pages),
                )
                
                # Directly build full page visual regions (no cropping needed, one per page)
                visual_regions = []
                for v_page in visual_pages:
                    visual_regions.append(
                        VisualRegion(
                            document_id=payload.document_id,
                            page_number=v_page.page_number,
                            region_index=0,
                            region_type="HANDWRITTEN" if document.document_type == "HANDWRITTEN_NOTES" or parsed.content_source_type == "HANDWRITTEN_SCAN" else "FULL_PAGE_VISUAL",
                            asset_path=v_page.image_path,
                            bbox_json=None,
                            page_asset_id=asset_ids_by_page.get(v_page.page_number),
                            width=v_page.width,
                            height=v_page.height,
                            confidence=0.85,
                        )
                    )
                self._repository.replace_visual_regions(payload.document_id, visual_regions)
                
                self._repository.mark_processing(payload.task_id, payload.document_id, "VLM_ANALYSIS", 65)
                vlm_results = analyze_regions_with_vlm(visual_regions, fail_on_error=strategy.require_vlm_success)
                self._repository.replace_vlm_results(payload.document_id, vlm_results)
                
                self._repository.mark_processing(payload.task_id, payload.document_id, "LAYOUT_CHUNKING", 75)
                layout_blocks = []
                for res in vlm_results:
                    layout_blocks.append(
                        LayoutBlock(
                            document_id=payload.document_id,
                            page_number=res.page_number,
                            block_index=0,
                            block_type="PARAGRAPH",
                            content=res.transcription or res.description or "",
                            bbox_json=None,
                            source_asset_id=asset_ids_by_page.get(res.page_number),
                            confidence=0.85,
                            metadata_json=json.dumps(
                                {
                                    "source": "page_level_vlm",
                                    "documentType": strategy.document_type,
                                    "contentSourceType": strategy.content_source_type,
                                    "markdownStrategy": strategy.markdown_strategy,
                                    "chunkStrategy": strategy.chunk_strategy,
                                },
                                separators=(",", ":"),
                            ),
                        )
                    )
                self._repository.replace_layout_blocks(payload.document_id, layout_blocks)
                
                markdown = build_markdown_document(payload.document_id, layout_blocks, vlm_results)
                self._repository.replace_markdown_pages(payload.document_id, markdown.pages)
                self._repository.save_markdown_document(markdown.document)
                
                chunks = build_markdown_chunks(
                    markdown.document.markdown,
                    layout_blocks,
                    vlm_results,
                    asset_ids_by_page,
                    parsed.content_source_type,
                    document.document_type,
                    strategy.chunk_strategy,
                )
                extracted_text_len = len(markdown.document.markdown)
                preview_text_source = markdown.document.markdown
            else:
                self._repository.mark_processing(payload.task_id, payload.document_id, "ANALYZING_VISUAL_CONTENT", 55)
                visual_pages = analyze_pdf_visuals(document.storage_path, payload.document_id)
                asset_ids_by_page = self._repository.replace_page_assets(
                    payload.document_id,
                    to_page_assets(payload.document_id, visual_pages),
                )
                self._repository.mark_processing(payload.task_id, payload.document_id, "CROPPING_VISUAL_REGIONS", 60)
                visual_regions = build_visual_regions(
                    document.storage_path,
                    payload.document_id,
                    document.document_type,
                    visual_pages,
                    asset_ids_by_page,
                )
                self._repository.replace_visual_regions(payload.document_id, visual_regions)

                self._repository.mark_processing(payload.task_id, payload.document_id, "VLM_ANALYSIS", 65)
                vlm_regions = select_regions_for_vlm(visual_regions, visual_pages, document.document_type)
                vlm_results = analyze_regions_with_vlm(vlm_regions)
                self._repository.replace_vlm_results(payload.document_id, vlm_results)

                self._repository.mark_processing(payload.task_id, payload.document_id, "LAYOUT_CHUNKING", 75)
                layout = build_layout_parse(
                    document.storage_path,
                    payload.document_id,
                    visual_pages,
                    asset_ids_by_page,
                    vlm_results,
                )
                self._repository.replace_layout_blocks(payload.document_id, layout.blocks)
                markdown = build_markdown_document(payload.document_id, layout.blocks, vlm_results)
                self._repository.replace_markdown_pages(payload.document_id, markdown.pages)
                self._repository.save_markdown_document(markdown.document)
                
                chunks = build_markdown_chunks(
                    markdown.document.markdown,
                    layout.blocks,
                    vlm_results,
                    asset_ids_by_page,
                    parsed.content_source_type,
                    document.document_type,
                    strategy.chunk_strategy,
                )
                extracted_text_len = len(layout.full_text or parsed.text)
                preview_text_source = layout.preview or parsed.preview

            parser_name = "gemini-page-level-vlm" if strategy.force_full_page_vlm else "pymupdf-layout-aware+vlm-assets"
            self._repository.save_parse_result(
                document_id=payload.document_id,
                parser_name=parser_name,
                page_count=parsed.page_count,
                extracted_text_length=extracted_text_len,
                extracted_text_preview=preview_text_source[:600],
                detected_content_source_type=parsed.content_source_type,
            )

            self._repository.mark_processing(payload.task_id, payload.document_id, "CHUNKING", 85)
            self._repository.replace_chunks(payload.document_id, chunks)
            self._repository.ensure_embedding_schema()
            self._repository.mark_completed(payload.task_id, payload.document_id)
        except Exception as exc:
            self._repository.mark_failed(payload.task_id, payload.document_id, str(exc))
            raise
