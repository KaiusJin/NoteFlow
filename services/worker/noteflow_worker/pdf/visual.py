import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz

from noteflow_worker.db.repository import PageAsset, TextChunk
from noteflow_worker.pdf.parser import estimate_tokens


RENDER_DPI = 144
MIN_DRAWINGS_FOR_VISUAL_PAGE = 8
MIN_IMAGE_COVERAGE = 0.04
MIN_OCR_CHARS_FOR_SUMMARY = 20


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


def analyze_pdf_visuals(path: str, document_id: str) -> list[VisualPage]:
    pdf_path = Path(path)
    output_dir = pdf_path.parent.parent / "rendered" / document_id
    output_dir.mkdir(parents=True, exist_ok=True)

    visual_pages: list[VisualPage] = []
    with fitz.open(str(pdf_path)) as document:
        for page_index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
            image_path = output_dir / f"page-{page_index:03d}.png"
            pixmap.save(str(image_path))

            image_blocks, image_coverage = image_block_stats(page)
            drawings = page.get_drawings()
            text = page.get_text("text") or ""
            ocr_text = run_ocr_if_available(image_path)

            visual_pages.append(
                VisualPage(
                    page_number=page_index,
                    image_path=str(image_path),
                    width=pixmap.width,
                    height=pixmap.height,
                    image_count=max(len(image_blocks), len(page.get_images(full=True))),
                    drawing_count=len(drawings),
                    image_coverage=image_coverage,
                    text_length=len(text),
                    ocr_text=ocr_text,
                )
            )
    return visual_pages


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


def run_ocr_if_available(image_path: Path) -> Optional[str]:
    if shutil.which("tesseract") is None:
        return None
    try:
        import pytesseract
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(image_path))
    except Exception:
        return None
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(cleaned) < MIN_OCR_CHARS_FOR_SUMMARY:
        return None
    return cleaned[:4000]


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


def build_visual_chunks(pages: list[VisualPage], asset_ids_by_page: dict[int, str]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page in pages:
        if not page.needs_visual_chunk:
            continue
        metadata = {
            "visual": True,
            "imageCount": page.image_count,
            "drawingCount": page.drawing_count,
            "imageCoverage": page.image_coverage,
            "ocrAvailable": page.ocr_text is not None,
        }
        content = page.summary
        chunks.append(
            TextChunk(
                page_number=page.page_number,
                chunk_index=0,
                content=content,
                section_title="Visual content",
                page_start=page.page_number,
                page_end=page.page_number,
                chunk_type="MIXED" if page.text_length else "IMAGE",
                token_count=estimate_tokens(content),
                source_asset_id=asset_ids_by_page.get(page.page_number),
                metadata_json=json.dumps(metadata, separators=(",", ":")),
            )
        )
    return chunks


def merge_and_reindex_chunks(text_chunks: list[TextChunk], visual_chunks: list[TextChunk]) -> list[TextChunk]:
    ordered = sorted(
        [*text_chunks, *visual_chunks],
        key=lambda chunk: (
            chunk.page_start or chunk.page_number,
            1 if chunk.chunk_type in {"IMAGE", "MIXED"} and chunk.source_asset_id else 0,
            chunk.chunk_index,
        ),
    )
    return [
        TextChunk(
            page_number=chunk.page_number,
            chunk_index=index,
            content=chunk.content,
            section_title=chunk.section_title,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            chunk_type=chunk.chunk_type,
            token_count=chunk.token_count,
            source_asset_id=chunk.source_asset_id,
            metadata_json=chunk.metadata_json,
        )
        for index, chunk in enumerate(ordered)
    ]
