from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Optional

import fitz

from noteflow_worker.db.repository import PageAsset
from noteflow_worker.pdf.ocr import clean_ocr_text, make_ocr_backend
from noteflow_worker.runtime.resource_pools import ResourcePoolPlan
from noteflow_worker.runtime.limits import process_resource_slot


RENDER_DPI = 144
MIN_DRAWINGS_FOR_VISUAL_PAGE = 8
MIN_IMAGE_COVERAGE = 0.04
MIN_NATIVE_TEXT_CHARS_TO_SKIP_OCR = 160
MIN_IMAGE_COVERAGE_FOR_OCR = 0.12
_gpu_backend_cache: list = []
_gpu_backend_cache_lock = Lock()


@dataclass(frozen=True)
class VisualPage:
    page_number: int
    image_path: str
    width: int
    height: int
    image_count: int
    drawing_count: int
    image_coverage: float
    text_length: int
    ocr_text: Optional[str]
    native_text_preview: str = ""

    @property
    def has_visual_content(self) -> bool:
        return (
            self.image_count > 0
            or self.drawing_count >= MIN_DRAWINGS_FOR_VISUAL_PAGE
            or self.image_coverage >= MIN_IMAGE_COVERAGE
        )

    @property
    def needs_visual_chunk(self) -> bool:
        return self.has_visual_content

    @property
    def summary(self) -> str:
        parts = [
            f"Rendered page image captured for page {self.page_number}.",
            f"Embedded images: {self.image_count}.",
            f"Vector drawing objects: {self.drawing_count}.",
            f"Estimated image coverage: {self.image_coverage:.1%}.",
        ]
        if self.ocr_text:
            parts.append("OCR text from rendered page:\n" + self.ocr_text)
        else:
            parts.append("No reliable local OCR text was produced for this page.")
        return "\n".join(parts)


def analyze_pdf_visuals(
    path: str,
    document_id: str,
    resource_plan: ResourcePoolPlan | None = None,
) -> list[VisualPage]:
    pdf_path = Path(path)
    output_dir = pdf_path.parent.parent / "rendered" / document_id
    output_dir.mkdir(parents=True, exist_ok=True)

    with fitz.open(str(pdf_path)) as document:
        page_count = len(document)
    render_workers = max(1, min(resource_plan.cpu_workers if resource_plan else 1, page_count or 1))
    batches = [list(range(start, page_count + 1, render_workers)) for start in range(1, render_workers + 1)]
    visual_pages: list[VisualPage] = []
    with ThreadPoolExecutor(max_workers=render_workers, thread_name_prefix="pdf-render") as executor:
        futures = [
            executor.submit(_render_page_batch_with_limit, str(pdf_path), output_dir, batch, render_workers)
            for batch in batches if batch
        ]
        for future in as_completed(futures):
            visual_pages.extend(future.result())
    visual_pages.sort(key=lambda page: page.page_number)

    backend = make_ocr_backend(resource_plan.accelerator if resource_plan else None)
    candidates = [
        page for page in visual_pages
        if should_run_ocr(
            "x" * page.text_length,
            [{}] * page.image_count,
            page.image_coverage,
        )
    ]
    if not candidates or backend.name == "disabled":
        return visual_pages
    configured_workers = (
        resource_plan.gpu_workers if backend.uses_gpu and resource_plan else
        resource_plan.cpu_workers if resource_plan else 1
    )
    workers = max(1, min(configured_workers, len(candidates)))
    backends = shared_ocr_backends(backend, workers, resource_plan)
    workers = len(backends)
    ocr_by_page: dict[int, str | None] = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"ocr-{backend.name}") as executor:
        futures = {
            executor.submit(
                recognize_with_global_limit,
                backends[index % len(backends)],
                page.image_path,
                workers,
            ): page.page_number
            for index, page in enumerate(candidates)
        }
        for future in as_completed(futures):
            page_number = futures[future]
            try:
                ocr_by_page[page_number] = clean_ocr_text(future.result())
            except Exception:
                ocr_by_page[page_number] = None
    return [replace(page, ocr_text=ocr_by_page.get(page.page_number)) for page in visual_pages]


def shared_ocr_backends(backend, workers: int, resource_plan: ResourcePoolPlan | None) -> list:
    if not backend.uses_gpu:
        return [backend] * workers
    with _gpu_backend_cache_lock:
        if not _gpu_backend_cache:
            _gpu_backend_cache.append(backend)
        while len(_gpu_backend_cache) < workers:
            try:
                _gpu_backend_cache.append(make_ocr_backend(resource_plan.accelerator if resource_plan else None))
            except (ImportError, RuntimeError, OSError):
                break
        return list(_gpu_backend_cache[:workers])


def recognize_with_global_limit(backend, image_path: str, workers: int) -> str:
    resource_name = "gpu_ocr" if backend.uses_gpu else "cpu_ocr"
    with process_resource_slot(resource_name, workers):
        return backend.recognize(image_path)


def _render_page_batch(pdf_path: str, output_dir: Path, page_numbers: list[int]) -> list[VisualPage]:
    rendered: list[VisualPage] = []
    with fitz.open(pdf_path) as document:
        for page_index in page_numbers:
            page = document[page_index - 1]
            pixmap = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
            image_path = output_dir / f"page-{page_index:03d}.png"
            pixmap.save(str(image_path))

            image_blocks, image_coverage = image_block_stats(page)
            drawings = page.get_drawings()
            text = page.get_text("text") or ""
            rendered.append(
                VisualPage(
                    page_number=page_index,
                    image_path=str(image_path),
                    width=pixmap.width,
                    height=pixmap.height,
                    image_count=max(len(image_blocks), len(page.get_images(full=True))),
                    drawing_count=len(drawings),
                    image_coverage=image_coverage,
                    text_length=len(text),
                    ocr_text=None,
                    native_text_preview=" ".join(text.split())[:2000],
                )
            )
    return rendered


def _render_page_batch_with_limit(
    pdf_path: str,
    output_dir: Path,
    page_numbers: list[int],
    render_workers: int,
) -> list[VisualPage]:
    with process_resource_slot("pdf_render", render_workers):
        return _render_page_batch(pdf_path, output_dir, page_numbers)
def should_run_ocr(text: str, image_blocks: list[dict], image_coverage: float) -> bool:
    if len(text.strip()) < MIN_NATIVE_TEXT_CHARS_TO_SKIP_OCR:
        return True
    return bool(image_blocks) and image_coverage >= MIN_IMAGE_COVERAGE_FOR_OCR


def image_block_stats(page) -> tuple[list[dict], float]:
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    image_blocks: list[dict] = []
    image_area = 0.0
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = bbox
        area = max(0.0, float(x1 - x0)) * max(0.0, float(y1 - y0))
        image_area += area
        image_blocks.append(block)
    return image_blocks, min(image_area / page_area, 1.0)
def to_page_assets(document_id: str, pages: list[VisualPage]) -> list[PageAsset]:
    return [
        PageAsset(
            document_id=document_id,
            page_number=page.page_number,
            asset_type="PAGE_RENDER",
            image_path=page.image_path,
            width=page.width,
            height=page.height,
            image_count=page.image_count,
            drawing_count=page.drawing_count,
            image_coverage=page.image_coverage,
            text_length=page.text_length,
            visual_summary=page.summary if page.has_visual_content else None,
        )
        for page in pages
    ]
