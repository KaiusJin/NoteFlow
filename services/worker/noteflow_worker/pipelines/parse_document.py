import json

from noteflow_worker.config import settings
from noteflow_worker.db.repository import Repository
from noteflow_worker.pdf.artifacts import cleanup_orphaned_pdf_artifacts
from noteflow_worker.pdf.layout import build_layout_parse, build_markdown_chunks
from noteflow_worker.pdf.markdown import build_markdown_document
from noteflow_worker.pdf.parser import ensure_pdf_exists, parse_pdf
from noteflow_worker.pdf.regions import analyze_regions_with_vlm, build_visual_regions, select_regions_for_vlm
from noteflow_worker.pdf.router import FULL_PAGE_VLM, build_document_route_plan
from noteflow_worker.pdf.strategies import resolve_processing_strategy
from noteflow_worker.pdf.visual import analyze_pdf_visuals, to_page_assets
from noteflow_worker.queue.redis_queue import TaskPayload
from noteflow_worker.runtime.resource_pools import build_resource_pool_plan


class ParseDocumentPipeline:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def run(self, payload: TaskPayload) -> None:
        try:
            self._repository.mark_processing(payload.task_id, payload.document_id, "PARSING_PDF", 10)
            document = self._repository.load_document(payload.document_id)
            ensure_pdf_exists(document.storage_path)

            resource_plan = build_resource_pool_plan(
                configured_cpu_workers=settings.pdf_cpu_workers,
                configured_io_workers=settings.pdf_io_workers,
                configured_gpu_workers=settings.pdf_gpu_workers,
                configured_vlm_workers=settings.vision_concurrent_requests,
                gpu_memory_per_task_mib=settings.pdf_gpu_memory_per_task_mib,
                gpu_memory_reserve_mib=settings.pdf_gpu_memory_reserve_mib,
                gpu_worker_cap=settings.pdf_gpu_worker_cap,
            )

            self._repository.mark_processing(payload.task_id, payload.document_id, "EXTRACTING_TEXT", 25)
            parsed = parse_pdf(document.storage_path, document.document_type)
            strategy = resolve_processing_strategy(document.document_type, parsed.content_source_type)

            self._repository.mark_processing(
                payload.task_id,
                payload.document_id,
                "ANALYZING_VISUAL_CONTENT",
                38,
            )
            visual_pages = analyze_pdf_visuals(document.storage_path, payload.document_id, resource_plan)
            route_plan = build_document_route_plan(document.document_type, parsed.page_profiles, visual_pages)
            parse_manifest = {
                "schemaVersion": "pdf-converter-v2",
                "documentType": document.document_type,
                "contentSource": {
                    "label": parsed.content_source_type,
                    "confidence": parsed.source_confidence,
                    "pageDistribution": parsed.source_distribution,
                },
                "pageRoutes": [
                    {
                        "page": page.page_number,
                        "mode": page.mode,
                        "requiredVlm": page.required_vlm,
                        "suppressNativeText": page.suppress_native_text,
                        "reasons": list(page.reasons),
                    }
                    for page in route_plan.pages
                ],
                "resourcePools": {
                    "cpuWorkers": resource_plan.cpu_workers,
                    "ioWorkers": resource_plan.io_workers,
                    "gpuWorkers": resource_plan.gpu_workers,
                    "vlmWorkers": resource_plan.vlm_workers,
                    "accelerator": resource_plan.accelerator.kind,
                    "rationale": resource_plan.rationale,
                },
            }
            self._repository.save_parse_manifest(
                payload.document_id,
                json.dumps(parse_manifest, separators=(",", ":")),
            )
            asset_ids_by_page = self._repository.replace_page_assets(
                payload.document_id,
                to_page_assets(payload.document_id, visual_pages),
            )

            self._repository.mark_processing(payload.task_id, payload.document_id, "CROPPING_VISUAL_REGIONS", 50)
            full_page_routes = {
                page.page_number: page.region_hint
                for page in route_plan.pages
                if page.mode == FULL_PAGE_VLM
            }
            visual_regions = build_visual_regions(
                document.storage_path,
                payload.document_id,
                document.document_type,
                visual_pages,
                asset_ids_by_page,
                full_page_routes=full_page_routes,
            )
            self._repository.replace_visual_regions(payload.document_id, visual_regions)

            self._repository.mark_processing(payload.task_id, payload.document_id, "VLM_ANALYSIS", 62)
            vlm_regions = select_regions_for_vlm(
                visual_regions,
                visual_pages,
                document.document_type,
                required_region_keys=route_plan.required_vlm_keys,
            )
            selected_keys = {(region.page_number, region.region_index) for region in vlm_regions}
            parse_manifest["visualAnalysis"] = {
                "discoveredRegionCount": len(visual_regions),
                "selectedRegionCount": len(vlm_regions),
                "requiredRegionCount": len(route_plan.required_vlm_keys),
                "skippedRegions": [
                    {"page": region.page_number, "region": region.region_index, "type": region.region_type}
                    for region in visual_regions
                    if (region.page_number, region.region_index) not in selected_keys
                ],
            }
            self._repository.save_parse_manifest(
                payload.document_id,
                json.dumps(parse_manifest, separators=(",", ":")),
            )
            self._repository.ensure_vlm_schema()
            existing_results = self._repository.load_vlm_results(payload.document_id)
            vlm_results = analyze_regions_with_vlm(
                vlm_regions,
                required_region_keys=route_plan.required_vlm_keys,
                existing_results=existing_results,
                persist_result=self._repository.upsert_vlm_result,
                max_workers=resource_plan.vlm_workers,
            )
            # Canonicalize the result set after incremental per-region commits.
            self._repository.replace_vlm_results(payload.document_id, vlm_results)

            self._repository.mark_processing(
                payload.task_id,
                payload.document_id,
                "LAYOUT_CHUNKING",
                76,
            )
            layout = build_layout_parse(
                document.storage_path,
                payload.document_id,
                visual_pages,
                asset_ids_by_page,
                vlm_results,
                suppress_native_text_pages=route_plan.suppress_native_text_pages,
                visual_regions=visual_regions,
            )
            self._repository.replace_layout_blocks(payload.document_id, layout.blocks)
            markdown = build_markdown_document(
                payload.document_id,
                layout.blocks,
                vlm_results,
                document_type=document.document_type,
            )
            self._repository.replace_markdown_pages(payload.document_id, markdown.pages)
            self._repository.save_markdown_document(markdown.document)

            self._repository.mark_processing(payload.task_id, payload.document_id, "CHUNKING", 88)
            chunks = build_markdown_chunks(
                markdown.document.markdown,
                layout.blocks,
                vlm_results,
                asset_ids_by_page,
                parsed.content_source_type,
                document.document_type,
                strategy.chunk_strategy,
            )
            self._repository.replace_chunks(payload.document_id, chunks)
            self._repository.save_parse_result(
                document_id=payload.document_id,
                parser_name="noteflow-page-router-v2",
                page_count=parsed.page_count,
                extracted_text_length=len(markdown.document.markdown),
                extracted_text_preview=(layout.preview or parsed.preview)[:600],
                detected_content_source_type=parsed.content_source_type,
                source_confidence=parsed.source_confidence,
                source_distribution_json=json.dumps(parsed.source_distribution, separators=(",", ":")),
            )

            if settings.pdf_cleanup_intermediate_files:
                cleanup_orphaned_pdf_artifacts(
                    document.storage_path,
                    payload.document_id,
                    visual_pages,
                    visual_regions,
                )

            self._repository.ensure_embedding_schema()
            self._repository.mark_completed(payload.task_id, payload.document_id)
            print(
                "PDF parse completed "
                + json.dumps(
                    {
                        "documentId": payload.document_id,
                        "sourceType": parsed.content_source_type,
                        "sourceConfidence": parsed.source_confidence,
                        "pageRoutes": {
                            mode: sum(page.mode == mode for page in route_plan.pages)
                            for mode in sorted({page.mode for page in route_plan.pages})
                        },
                        "resourcePools": {
                            "cpu": resource_plan.cpu_workers,
                            "io": resource_plan.io_workers,
                            "gpu": resource_plan.gpu_workers,
                            "vlm": resource_plan.vlm_workers,
                        },
                    },
                    separators=(",", ":"),
                )
            )
        except Exception as exc:
            self._repository.mark_failed(payload.task_id, payload.document_id, str(exc))
            raise
