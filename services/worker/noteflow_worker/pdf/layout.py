import json
import re
from dataclasses import dataclass
from typing import Optional

import fitz

from noteflow_worker.db.repository import LayoutBlock, TextChunk, VlmResult
from noteflow_worker.pdf.parser import (
    MAX_TOKENS,
    TARGET_TOKENS,
    classify_line,
    estimate_tokens,
    preview_text,
)
from noteflow_worker.pdf.math_normalizer import balance_cases_environment, normalize_pdf_math_text
from noteflow_worker.pdf.visual import VisualPage


TEXT_BLOCK_TYPES = {"PARAGRAPH", "HEADING", "LIST", "CODE", "FORMULA", "TABLE"}
VISUAL_STANDALONE_COVERAGE = 0.18
PAGE_AWARE_SOURCE_TYPES = {"SCANNED_PDF", "HANDWRITTEN_SCAN"}
PAGE_AWARE_TARGET_TOKENS = 650
PAGE_AWARE_MAX_TOKENS = 1000
PAGE_AWARE_MIN_MERGE_TOKENS = 60
STRATEGY_TOKEN_BUDGETS = {
    "PAGE_AWARE": {"target": 650, "max": 1000, "min_merge": 60},
    "SLIDE_AWARE": {"target": 700, "max": 1100, "min_merge": 80},
    "TOPIC_AWARE": {"target": 650, "max": 1000, "min_merge": 80},
    "PAPER_SECTION_AWARE": {"target": 800, "max": 1200, "min_merge": 100},
    "QUESTION_AWARE": {"target": 750, "max": 1200, "min_merge": 80},
    "MIXED_FALLBACK": {"target": 650, "max": 1000, "min_merge": 80},
}


@dataclass(frozen=True)
class LayoutParseResult:
    blocks: list[LayoutBlock]
    chunks: list[TextChunk]
    full_text: str
    preview: str


@dataclass(frozen=True)
class WorkingBlock:
    page_number: int
    order: tuple[float, float, int]
    block_type: str
    content: str
    bbox: Optional[list[float]]
    section_title: Optional[str]
    heading_path: list[str]
    source_asset_id: Optional[str]
    confidence: float
    metadata: dict

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.content)


def build_layout_parse(
    path: str,
    document_id: str,
    visual_pages: list[VisualPage],
    page_asset_ids: dict[int, str],
    vlm_results: list[VlmResult] | None = None,
) -> LayoutParseResult:
    visual_by_page = {page.page_number: page for page in visual_pages}
    vlm_by_page: dict[int, list[VlmResult]] = {}
    for result in vlm_results or []:
        vlm_by_page.setdefault(result.page_number, []).append(result)
    working_blocks: list[WorkingBlock] = []
    current_heading: Optional[str] = None
    heading_path: list[str] = []

    with fitz.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            page_blocks = extract_page_text_blocks(page, page_index)
            for block in page_blocks:
                if block.block_type == "HEADING":
                    current_heading = block.content
                    heading_path = update_heading_path(heading_path, block.content)
                    block = WorkingBlock(
                        page_number=block.page_number,
                        order=block.order,
                        block_type=block.block_type,
                        content=block.content,
                        bbox=block.bbox,
                        section_title=current_heading,
                        heading_path=heading_path,
                        source_asset_id=block.source_asset_id,
                        confidence=block.confidence,
                        metadata=block.metadata,
                    )
                elif block.block_type in TEXT_BLOCK_TYPES:
                    block = WorkingBlock(
                        page_number=block.page_number,
                        order=block.order,
                        block_type=block.block_type,
                        content=block.content,
                        bbox=block.bbox,
                        section_title=current_heading,
                        heading_path=heading_path,
                        source_asset_id=block.source_asset_id,
                        confidence=block.confidence,
                        metadata=block.metadata,
                    )
                working_blocks.append(block)

            visual_page = visual_by_page.get(page_index)
            if visual_page and visual_page.has_visual_content:
                working_blocks.extend(
                    visual_working_blocks(
                        visual_page,
                        page_asset_ids.get(page_index),
                        vlm_by_page.get(page_index, []),
                        current_heading,
                        heading_path,
                    )
                )

    working_blocks = sorted(working_blocks, key=lambda block: (block.page_number, *block.order))
    working_blocks = mark_layout_boilerplate(working_blocks)
    layout_blocks = to_layout_blocks(document_id, working_blocks)
    semantic_blocks = [block for block in working_blocks if block.block_type != "BOILERPLATE"]
    chunks = build_structural_chunks(semantic_blocks)
    full_text = "\n\n".join(block.content for block in semantic_blocks if block.content)
    preview_source = "\n\n".join(chunk.content for chunk in chunks[:3]) if chunks else full_text
    return LayoutParseResult(
        blocks=layout_blocks,
        chunks=chunks,
        full_text=full_text,
        preview=preview_text(preview_source),
    )


def extract_page_text_blocks(page, page_number: int) -> list[WorkingBlock]:
    extracted: list[WorkingBlock] = []
    text_dict = page.get_text("dict", sort=True)
    for block_index, block in enumerate(text_dict.get("blocks", [])):
        if block.get("type") != 0:
            continue
        lines = block_lines(block)
        if not lines:
            continue
        raw_text = normalize_pdf_math_text("\n".join(lines))
        normalized_lines = [line for line in raw_text.splitlines() if line.strip()]
        block_type = classify_text_block(normalized_lines)
        content = format_block_content(raw_text, block_type)
        bbox = normalize_bbox(block.get("bbox"))
        y0 = bbox[1] if bbox else 0.0
        x0 = bbox[0] if bbox else 0.0
        extracted.append(
            WorkingBlock(
                page_number=page_number,
                order=(y0, x0, block_index),
                block_type=block_type,
                content=content,
                bbox=bbox,
                section_title=None,
                heading_path=[],
                source_asset_id=None,
                confidence=0.78,
                metadata={
                    "source": "pymupdf_text_block",
                    "lineCount": len(lines),
                    "rawType": block.get("type"),
                },
            )
        )
    return merge_adjacent_small_text_blocks(extracted)


def block_lines(block: dict) -> list[str]:
    lines: list[str] = []
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        text = "".join(span.get("text", "") for span in spans).strip()
        if text:
            lines.append(text)
    return lines


def classify_text_block(lines: list[str]) -> str:
    non_empty = [line.strip() for line in lines if line.strip()]
    if not non_empty:
        return "PARAGRAPH"
    line_types = [classify_line(line) for line in non_empty]
    if is_markdown_table_candidate(non_empty):
        return "TABLE"
    if line_types.count("CODE") >= max(1, len(non_empty) // 2):
        return "CODE"
    if line_types.count("FORMULA") >= max(1, len(non_empty) // 2):
        return "FORMULA"
    if len(non_empty) <= 2 and line_types[0] == "HEADING":
        return "HEADING"
    if line_types.count("LIST") >= max(1, len(non_empty) // 2):
        return "LIST"
    return most_common_text_type(line_types)


def most_common_text_type(line_types: list[str]) -> str:
    ranked = sorted(
        ((line_types.count(item), item) for item in set(line_types)),
        reverse=True,
    )
    block_type = ranked[0][1]
    return "PARAGRAPH" if block_type in {"LIST", "HEADING"} else block_type


def is_markdown_table_candidate(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    split_rows = [split_table_row(line) for line in lines]
    rows_with_columns = [row for row in split_rows if len(row) >= 2]
    return len(rows_with_columns) >= 2 and same_column_shape(rows_with_columns)


def same_column_shape(rows: list[list[str]]) -> bool:
    column_counts = [len(row) for row in rows]
    return max(column_counts) - min(column_counts) <= 1


def split_table_row(line: str) -> list[str]:
    if "|" in line:
        return [cell.strip() for cell in line.strip("|").split("|") if cell.strip()]
    return [cell.strip() for cell in re.split(r"\s{2,}", line.strip()) if cell.strip()]


def format_block_content(text: str, block_type: str) -> str:
    text = normalize_pdf_math_text(text)
    if "\\begin{cases}" in text or "\\end{cases}" in text:
        block_type = "FORMULA"
        text = balance_cases_environment(text)
    if block_type == "TABLE":
        return table_to_markdown(text)
    if block_type == "CODE":
        return "```text\n" + text.strip() + "\n```"
    if block_type == "FORMULA":
        return "$$\n" + text.strip() + "\n$$"
    return text.strip()


def table_to_markdown(text: str) -> str:
    rows = [split_table_row(line) for line in text.splitlines() if line.strip()]
    rows = [row for row in rows if row]
    if not rows:
        return text.strip()
    max_cols = max(len(row) for row in rows)
    normalized = [row + [""] * (max_cols - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * max_cols
    body = normalized[1:]
    markdown_rows = [header, separator, *body]
    return "\n".join("| " + " | ".join(cell.strip() for cell in row) + " |" for row in markdown_rows)


def normalize_bbox(value) -> Optional[list[float]]:
    if not value or len(value) != 4:
        return None
    return [round(float(item), 2) for item in value]


def update_heading_path(current: list[str], heading: str) -> list[str]:
    normalized = heading.strip()
    if not normalized:
        return current
    if ":" in normalized or len(normalized.split()) <= 6:
        return [normalized]
    return [*current[:1], normalized] if current else [normalized]


def visual_working_blocks(
    page: VisualPage,
    asset_id: Optional[str],
    vlm_results: list[VlmResult],
    section_title: Optional[str],
    heading_path: list[str],
) -> list[WorkingBlock]:
    useful_results = [result for result in vlm_results if result.search_text or result.description or result.transcription]
    if useful_results:
        return [
            visual_region_working_block(page, asset_id, result, section_title, heading_path, order_index)
            for order_index, result in enumerate(useful_results)
        ]
    block_type = "MIXED_VISUAL" if page.text_length else "IMAGE"
    metadata = {
        "source": "page_render_ocr",
        "containsImage": page.image_count > 0,
        "containsDrawing": page.drawing_count > 0,
        "imageCount": page.image_count,
        "drawingCount": page.drawing_count,
        "imageCoverage": page.image_coverage,
        "ocrAvailable": page.ocr_text is not None,
        "vlmStatus": "completed" if useful_results else ("failed" if vlm_results else "not_configured"),
        "vlmRegionCount": len(vlm_results),
    }
    if page.image_coverage >= VISUAL_STANDALONE_COVERAGE:
        metadata["standaloneVisual"] = True
    content = visual_block_content(page, useful_results)
    return [
        WorkingBlock(
            page_number=page.page_number,
            order=(10_000.0, 0.0, 0),
            block_type=block_type,
            content=content,
            bbox=None,
            section_title=section_title,
            heading_path=heading_path,
            source_asset_id=asset_id,
            confidence=0.72 if page.ocr_text else 0.52,
            metadata=metadata,
        )
    ]


def visual_region_working_block(
    page: VisualPage,
    asset_id: Optional[str],
    result: VlmResult,
    section_title: Optional[str],
    heading_path: list[str],
    order_index: int,
) -> WorkingBlock:
    metadata = {
        "source": "vlm_region_analysis",
        "containsImage": True,
        "containsDrawing": page.drawing_count > 0,
        "imageCount": page.image_count,
        "drawingCount": page.drawing_count,
        "imageCoverage": page.image_coverage,
        "ocrAvailable": page.ocr_text is not None,
        "vlmStatus": "completed",
        "vlmProvider": result.provider,
        "vlmModel": result.model,
        "regionIndex": result.region_index,
        "regionType": result.region_type,
        "standaloneVisual": True,
    }
    return WorkingBlock(
        page_number=page.page_number,
        order=(10_000.0, float(order_index), result.region_index),
        block_type="MIXED_VISUAL",
        content=visual_region_content(page, result),
        bbox=None,
        section_title=section_title,
        heading_path=heading_path,
        source_asset_id=asset_id,
        confidence=0.86,
        metadata=metadata,
    )


def visual_region_content(page: VisualPage, result: VlmResult) -> str:
    parts = [
        f"Rendered visual region from page {page.page_number}.",
        f"Region type: {result.region_type}.",
        f"Embedded images on page: {page.image_count}.",
        f"Vector drawing objects on page: {page.drawing_count}.",
    ]
    parts.extend(
        item
        for item in [
            "Transcription:\n" + result.transcription if result.transcription else "",
            "Description:\n" + result.description if result.description else "",
            "LaTeX:\n" + result.latex if result.latex else "",
            "Code:\n```text\n" + result.code + "\n```" if result.code else "",
            "Search text:\n" + result.search_text if result.search_text else "",
            "Uncertainty:\n" + result.uncertainty if result.uncertainty else "",
        ]
        if item
    )
    return "\n\n".join(parts)


def visual_block_content(page: VisualPage, vlm_results: list[VlmResult]) -> str:
    if not vlm_results:
        return page.summary
    parts = [
        f"Rendered page image captured for page {page.page_number}.",
        f"Embedded images: {page.image_count}.",
        f"Vector drawing objects: {page.drawing_count}.",
        f"Estimated image coverage: {page.image_coverage:.1%}.",
    ]
    for result in vlm_results:
        parts.append(
            "\n".join(
                item
                for item in [
                    f"[Visual region {result.region_index}: {result.region_type}]",
                    "Transcription:\n" + result.transcription if result.transcription else "",
                    "Description:\n" + result.description if result.description else "",
                    "LaTeX:\n" + result.latex if result.latex else "",
                    "Code:\n```text\n" + result.code + "\n```" if result.code else "",
                    "Search text:\n" + result.search_text if result.search_text else "",
                    "Uncertainty:\n" + result.uncertainty if result.uncertainty else "",
                ]
                if item
            )
        )
    if page.ocr_text:
        parts.append("Local OCR fallback:\n" + page.ocr_text)
    return "\n\n".join(parts)


def merge_adjacent_small_text_blocks(blocks: list[WorkingBlock]) -> list[WorkingBlock]:
    merged: list[WorkingBlock] = []
    buffer: list[WorkingBlock] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        if len(buffer) == 1:
            merged.append(buffer[0])
        else:
            first = buffer[0]
            content = "\n".join(item.content for item in buffer)
            merged.append(
                WorkingBlock(
                    page_number=first.page_number,
                    order=first.order,
                    block_type="PARAGRAPH",
                    content=content,
                    bbox=union_bbox([item.bbox for item in buffer]),
                    section_title=first.section_title,
                    heading_path=first.heading_path,
                    source_asset_id=None,
                    confidence=min(item.confidence for item in buffer),
                    metadata={"source": "merged_small_text_blocks", "blockCount": len(buffer)},
                )
            )
        buffer = []

    for block in blocks:
        if block.block_type in {"HEADING", "CODE", "FORMULA", "TABLE"} or block.token_count >= 80:
            flush()
            merged.append(block)
            continue
        buffer.append(block)
        if sum(item.token_count for item in buffer) >= 120:
            flush()
    flush()
    return merged


def mark_layout_boilerplate(blocks: list[WorkingBlock]) -> list[WorkingBlock]:
    pages_by_fingerprint: dict[str, set[int]] = {}
    for block in blocks:
        if not can_be_layout_boilerplate(block):
            continue
        fingerprint = layout_boilerplate_fingerprint(block.content)
        if not fingerprint:
            continue
        pages_by_fingerprint.setdefault(fingerprint, set()).add(block.page_number)

    repeated = {
        fingerprint
        for fingerprint, pages in pages_by_fingerprint.items()
        if len(pages) >= 3
    }
    marked: list[WorkingBlock] = []
    for block in blocks:
        fingerprint = layout_boilerplate_fingerprint(block.content)
        if fingerprint in repeated and can_be_layout_boilerplate(block):
            marked.append(
                WorkingBlock(
                    page_number=block.page_number,
                    order=block.order,
                    block_type="BOILERPLATE",
                    content=block.content,
                    bbox=block.bbox,
                    section_title=block.section_title,
                    heading_path=block.heading_path,
                    source_asset_id=block.source_asset_id,
                    confidence=block.confidence,
                    metadata={**block.metadata, "excludedFromChunks": True},
                )
            )
        else:
            marked.append(block)
    return marked


def can_be_layout_boilerplate(block: WorkingBlock) -> bool:
    if block.source_asset_id:
        return False
    if block.block_type not in {"PARAGRAPH", "HEADING"}:
        return False
    if block.token_count > 35:
        return False
    text = block.content.strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 4:
        return False
    return True


def layout_boilerplate_fingerprint(text: str) -> str:
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    cleaned_lines: list[str] = []
    for line in lines:
        if re.match(r"^\d+\s*/\s*\d+$", line):
            continue
        line = re.sub(r"\d+", "#", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def union_bbox(boxes: list[Optional[list[float]]]) -> Optional[list[float]]:
    present = [box for box in boxes if box]
    if not present:
        return None
    return [
        min(box[0] for box in present),
        min(box[1] for box in present),
        max(box[2] for box in present),
        max(box[3] for box in present),
    ]


def to_layout_blocks(document_id: str, blocks: list[WorkingBlock]) -> list[LayoutBlock]:
    counters: dict[int, int] = {}
    output: list[LayoutBlock] = []
    for block in blocks:
        index = counters.get(block.page_number, 0)
        counters[block.page_number] = index + 1
        output.append(
            LayoutBlock(
                document_id=document_id,
                page_number=block.page_number,
                block_index=index,
                block_type=block.block_type,
                content=block.content,
                bbox_json=json.dumps(block.bbox, separators=(",", ":")) if block.bbox else None,
                section_title=block.section_title,
                heading_path_json=json.dumps(block.heading_path, separators=(",", ":")),
                source_asset_id=block.source_asset_id,
                confidence=block.confidence,
                metadata_json=json.dumps(block.metadata, separators=(",", ":")),
            )
        )
    return output


def build_structural_chunks(blocks: list[WorkingBlock]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current: list[WorkingBlock] = []

    def current_tokens() -> int:
        return sum(block.token_count for block in current)

    def flush() -> None:
        nonlocal current
        if not current:
            return
        chunks.append(chunk_from_working_blocks(current, len(chunks)))
        current = []

    for block in blocks:
        if should_be_standalone(block):
            flush()
            chunks.append(chunk_from_working_blocks([block], len(chunks)))
            continue

        if not current:
            current.append(block)
            continue

        section_changed = (
            block.block_type == "HEADING"
            or (
                block.section_title
                and current[-1].section_title
                and block.section_title != current[-1].section_title
            )
        )
        too_large = current_tokens() + block.token_count > MAX_TOKENS
        enough_context = current_tokens() >= TARGET_TOKENS and block.page_number != current[-1].page_number

        current_is_only_heading = current and all(item.block_type == "HEADING" for item in current)
        if (section_changed or too_large or enough_context) and not current_is_only_heading:
            flush()
        current.append(block)

    flush()
    return add_semantic_overlap(chunks)


def should_be_standalone(block: WorkingBlock) -> bool:
    if block.block_type == "TABLE":
        return True
    if block.block_type in {"CODE", "FORMULA"} and block.token_count >= 120:
        return True
    if block.block_type in {"IMAGE", "MIXED_VISUAL"}:
        return bool(block.metadata.get("standaloneVisual")) or block.token_count >= 80
    return False


def chunk_from_working_blocks(blocks: list[WorkingBlock], chunk_index: int) -> TextChunk:
    page_start = min(block.page_number for block in blocks)
    page_end = max(block.page_number for block in blocks)
    chunk_type = dominant_type(blocks)
    content = "\n\n".join(block.content for block in blocks if block.content)
    asset_ids = sorted({block.source_asset_id for block in blocks if block.source_asset_id})
    metadata = {
        "headings": first_heading_path(blocks),
        "blockTypes": sorted({block.block_type for block in blocks}),
        "containsImage": any(block.block_type in {"IMAGE", "MIXED_VISUAL"} for block in blocks),
        "containsTable": any(block.block_type == "TABLE" for block in blocks),
        "containsFormula": any(block.block_type == "FORMULA" for block in blocks),
        "containsCode": any(block.block_type == "CODE" for block in blocks),
        "assetIds": asset_ids,
        "bboxRefs": [
            {"page": block.page_number, "bbox": block.bbox, "type": block.block_type}
            for block in blocks
            if block.bbox
        ],
    }
    return TextChunk(
        page_number=page_start,
        chunk_index=chunk_index,
        content=content,
        section_title=first_non_empty(block.section_title for block in blocks),
        page_start=page_start,
        page_end=page_end,
        chunk_type=chunk_type,
        token_count=estimate_tokens(content),
        source_asset_id=asset_ids[0] if len(asset_ids) == 1 else None,
        metadata_json=json.dumps(metadata, separators=(",", ":")),
    )


def dominant_type(blocks: list[WorkingBlock]) -> str:
    types = [block.block_type for block in blocks if block.block_type != "HEADING"]
    if not types:
        return "HEADING"
    if any(item in {"IMAGE", "MIXED_VISUAL"} for item in types) and len(set(types)) > 1:
        return "MIXED"
    if any(item in {"IMAGE", "MIXED_VISUAL"} for item in types):
        return "MIXED_VISUAL"
    if "TABLE" in types:
        return "TABLE"
    if "CODE" in types:
        return "CODE"
    if "FORMULA" in types:
        return "FORMULA"
    return "PARAGRAPH"


def first_heading_path(blocks: list[WorkingBlock]) -> list[str]:
    for block in blocks:
        if block.heading_path:
            return block.heading_path
    return []


def first_non_empty(values) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def add_semantic_overlap(chunks: list[TextChunk]) -> list[TextChunk]:
    if len(chunks) <= 1:
        return chunks
    output: list[TextChunk] = []
    previous_tail = ""
    for index, chunk in enumerate(chunks):
        content = chunk.content
        metadata = json.loads(chunk.metadata_json or "{}")
        if previous_tail and chunk.chunk_type in {"PARAGRAPH", "MIXED"}:
            content = previous_tail + "\n\n" + content
            metadata["hasOverlap"] = True
        output.append(
            TextChunk(
                page_number=chunk.page_number,
                chunk_index=index,
                content=content,
                section_title=chunk.section_title,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                chunk_type=chunk.chunk_type,
                token_count=estimate_tokens(content),
                source_asset_id=chunk.source_asset_id,
                metadata_json=json.dumps(metadata, separators=(",", ":")),
            )
        )
        previous_tail = overlap_tail(chunk.content, chunk.chunk_type)
    return output


def overlap_tail(content: str, chunk_type: str) -> str:
    if chunk_type in {"CODE", "TABLE", "FORMULA", "MIXED_VISUAL"}:
        return ""
    words = content.split()
    if len(words) < 90:
        return ""
    return " ".join(words[-90:])


@dataclass
class MarkdownElement:
    content: str
    block_type: str  # HEADING, PARAGRAPH, CODE, FORMULA, TABLE, MIXED_VISUAL
    page_number: int
    heading_path: list[str]


def parse_markdown_to_elements(markdown_text: str) -> list[MarkdownElement]:
    lines = markdown_text.splitlines()
    elements: list[MarkdownElement] = []
    
    current_page = 1
    heading_stack: list[str] = []
    
    # State tracking
    in_code = False
    in_math = False
    in_figure = False
    in_table = False
    
    buffer: list[str] = []
    
    def flush():
        if not buffer:
            return
        content = "\n".join(buffer).strip()
        if content:
            if in_code:
                b_type = "CODE"
            elif in_math:
                b_type = "FORMULA"
            elif in_figure:
                b_type = "MIXED_VISUAL"
            elif in_table:
                b_type = "TABLE"
            else:
                b_type = "PARAGRAPH"
            elements.append(MarkdownElement(
                content=content,
                block_type=b_type,
                page_number=current_page,
                heading_path=list(heading_stack),
            ))
        buffer.clear()

    for line in lines:
        stripped = line.strip()
        
        # 1. Page Marker Comment
        page_match = re.match(r"^<!--\s*page\s*:\s*(\d+)\s*-->", stripped, re.IGNORECASE)
        if page_match:
            flush()
            in_code = in_math = in_figure = in_table = False
            current_page = int(page_match.group(1))
            continue

        # If inside a special block, keep appending
        if in_code:
            buffer.append(line)
            if stripped.startswith("```") and len(buffer) > 1:
                flush()
                in_code = False
            continue

        if in_math:
            buffer.append(line)
            if stripped.startswith("$$") and len(buffer) > 1:
                flush()
                in_math = False
            continue

        if in_figure:
            buffer.append(line)
            if "</figure>" in stripped:
                flush()
                in_figure = False
            continue

        # 2. Heading
        heading_match = re.match(r"^(#+)\s+(.*)$", stripped)
        if heading_match:
            flush()
            in_table = False
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            
            # Update heading stack
            if len(heading_stack) >= level:
                heading_stack = heading_stack[:level-1]
            heading_stack.append(title)
            
            elements.append(MarkdownElement(
                content=line,
                block_type="HEADING",
                page_number=current_page,
                heading_path=list(heading_stack),
            ))
            continue

        # 3. Enter special blocks
        if stripped.startswith("```"):
            flush()
            in_table = False
            in_code = True
            buffer.append(line)
            continue

        if stripped.startswith("$$"):
            flush()
            in_table = False
            in_math = True
            buffer.append(line)
            continue

        if stripped.startswith("<figure") or "<figure" in stripped:
            flush()
            in_table = False
            in_figure = True
            buffer.append(line)
            if "</figure>" in stripped:
                flush()
                in_figure = False
            continue

        # 4. Table lines
        is_table_line = "|" in line
        if is_table_line:
            if not in_table:
                flush()
                in_table = True
            buffer.append(line)
            continue
        else:
            if in_table:
                flush()
                in_table = False

        # 5. Page dividers (horizontal rules)
        if stripped == "---":
            flush()
            continue

        # 6. Paragraphs / list items / empty lines
        if not stripped:
            flush()
            continue

        buffer.append(line)

    flush()
    return elements


def normalize_text_for_matching(text: str) -> str:
    return re.sub(r"\W+", "", text).lower()


def link_element_metadata(
    element: MarkdownElement,
    layout_blocks: list[LayoutBlock],
    vlm_results: list[VlmResult],
) -> tuple[Optional[list[float]], Optional[str]]:
    norm_content = normalize_text_for_matching(element.content)
    if not norm_content:
        return None, None

    page_blocks = [b for b in layout_blocks if b.page_number == element.page_number]

    best_bbox = None
    best_asset_id = None
    
    # Try matching against layout blocks on the page
    for b in page_blocks:
        norm_b = normalize_text_for_matching(b.content or "")
        if norm_b and (norm_b in norm_content or norm_content in norm_b):
            if b.bbox_json:
                try:
                    best_bbox = json.loads(b.bbox_json)
                except Exception:
                    pass
            if b.source_asset_id:
                best_asset_id = b.source_asset_id
            break

    return best_bbox, best_asset_id


def should_markdown_element_be_standalone(element: MarkdownElement, token_count: int) -> bool:
    if element.block_type == "TABLE" and token_count >= 120:
        return True
    if element.block_type in {"CODE", "FORMULA"} and token_count >= 120:
        return True
    if element.block_type == "MIXED_VISUAL":
        return True
    return False


def dominant_markdown_type(elements: list[MarkdownElement]) -> str:
    types = [el.block_type for el in elements if el.block_type != "HEADING"]
    if not types:
        return "HEADING"
    if len(set(types)) > 1:
        return "MIXED"
    if any(item == "MIXED_VISUAL" for item in types) and len(set(types)) > 1:
        return "MIXED"
    if any(item == "MIXED_VISUAL" for item in types):
        return "MIXED_VISUAL"
    if "TABLE" in types:
        return "TABLE"
    if "CODE" in types:
        return "CODE"
    if "FORMULA" in types:
        return "FORMULA"
    return "PARAGRAPH"


def build_markdown_chunks(
    markdown_text: str,
    layout_blocks: list[LayoutBlock],
    vlm_results: list[VlmResult],
    asset_ids_by_page: dict[int, str],
    content_source_type: Optional[str] = None,
    document_type: Optional[str] = None,
    chunk_strategy: Optional[str] = None,
) -> list[TextChunk]:
    elements = parse_markdown_to_elements(markdown_text)
    resolved_strategy = chunk_strategy or infer_chunk_strategy(document_type, content_source_type, vlm_results)
    strategy_context = {
        "documentType": document_type or "OTHER",
        "contentSourceType": content_source_type or "UNKNOWN",
        "chunkStrategy": resolved_strategy,
    }
    if resolved_strategy in {"PAGE_AWARE", "SLIDE_AWARE"} or should_use_page_aware_chunks(content_source_type, vlm_results):
        return build_page_aware_markdown_chunks(
            elements,
            layout_blocks,
            vlm_results,
            asset_ids_by_page,
            resolved_strategy,
            strategy_context,
        )
    if resolved_strategy == "QUESTION_AWARE":
        return build_question_aware_markdown_chunks(
            elements,
            layout_blocks,
            vlm_results,
            asset_ids_by_page,
            strategy_context,
        )
    
    chunks: list[TextChunk] = []
    current: list[MarkdownElement] = []
    budgets = token_budgets(resolved_strategy)
    
    def current_tokens() -> int:
        return sum(estimate_tokens(el.content) for el in current)
        
    def flush() -> None:
        nonlocal current
        if not current:
            return
        chunks.append(chunk_from_elements(current, len(chunks), layout_blocks, vlm_results, asset_ids_by_page, strategy_context))
        current = []

    for element in elements:
        token_count = estimate_tokens(element.content)
        
        if should_markdown_element_be_standalone(element, token_count):
            flush()
            chunks.append(chunk_from_elements([element], len(chunks), layout_blocks, vlm_results, asset_ids_by_page, strategy_context))
            continue
            
        if not current:
            current.append(element)
            continue
            
        section_changed = (
            element.block_type == "HEADING"
            or (
                element.heading_path
                and current[-1].heading_path
                and element.heading_path[0] != current[-1].heading_path[0]
            )
        )
        too_large = current_tokens() + token_count > budgets["max"]
        enough_context = current_tokens() >= budgets["target"] and element.page_number != current[-1].page_number
        strategy_boundary = should_start_strategy_boundary(resolved_strategy, element, current)
        
        current_is_only_heading = all(el.block_type == "HEADING" for el in current)
        if (section_changed or too_large or enough_context or strategy_boundary) and not current_is_only_heading:
            flush()
            
        current.append(element)

    flush()
    return add_semantic_overlap(chunks)


def infer_chunk_strategy(
    document_type: Optional[str],
    content_source_type: Optional[str],
    vlm_results: list[VlmResult],
) -> str:
    if document_type == "HANDWRITTEN_NOTES" or content_source_type in PAGE_AWARE_SOURCE_TYPES:
        return "PAGE_AWARE"
    if document_type == "LECTURE_SLIDES":
        return "SLIDE_AWARE"
    if document_type == "RESEARCH_PAPER":
        return "PAPER_SECTION_AWARE"
    if document_type in {"ASSIGNMENT", "PAST_EXAM"}:
        return "QUESTION_AWARE"
    if document_type == "COURSE_NOTES":
        return "TOPIC_AWARE"
    if should_use_page_aware_chunks(content_source_type, vlm_results):
        return "PAGE_AWARE"
    return "MIXED_FALLBACK"


def token_budgets(chunk_strategy: Optional[str]) -> dict[str, int]:
    return STRATEGY_TOKEN_BUDGETS.get(chunk_strategy or "MIXED_FALLBACK", STRATEGY_TOKEN_BUDGETS["MIXED_FALLBACK"])


def should_use_page_aware_chunks(content_source_type: Optional[str], vlm_results: list[VlmResult]) -> bool:
    if content_source_type in PAGE_AWARE_SOURCE_TYPES:
        return True
    full_page_results = [
        result
        for result in vlm_results
        if result.region_type in {"FULL_PAGE_VISUAL", "HANDWRITTEN"}
    ]
    return bool(full_page_results) and len(full_page_results) == len(vlm_results)


def build_page_aware_markdown_chunks(
    elements: list[MarkdownElement],
    layout_blocks: list[LayoutBlock],
    vlm_results: list[VlmResult],
    asset_ids_by_page: dict[int, str],
    chunk_strategy: str = "PAGE_AWARE",
    strategy_context: Optional[dict] = None,
) -> list[TextChunk]:
    pages: list[list[MarkdownElement]] = []
    current_page: list[MarkdownElement] = []
    current_page_number: Optional[int] = None

    for element in elements:
        if current_page_number is None:
            current_page_number = element.page_number
        if element.page_number != current_page_number:
            if page_has_content(current_page):
                pages.append(current_page)
            current_page = []
            current_page_number = element.page_number
        current_page.append(element)

    if page_has_content(current_page):
        pages.append(current_page)

    chunks: list[TextChunk] = []
    current: list[MarkdownElement] = []
    budgets = token_budgets(chunk_strategy)

    def current_tokens() -> int:
        return sum(estimate_tokens(element.content) for element in current)

    def flush() -> None:
        nonlocal current
        if not current:
            return
        chunks.append(chunk_from_elements(current, len(chunks), layout_blocks, vlm_results, asset_ids_by_page, strategy_context))
        current = []

    for page_elements in pages:
        page_tokens = sum(estimate_tokens(element.content) for element in page_elements)
        if page_tokens > budgets["max"]:
            flush()
            for part in split_large_page(page_elements, budgets["max"]):
                chunks.append(chunk_from_elements(part, len(chunks), layout_blocks, vlm_results, asset_ids_by_page, strategy_context))
            continue

        if not current:
            current.extend(page_elements)
            continue

        combined_tokens = current_tokens() + page_tokens
        new_topic = starts_new_page_topic(page_elements)
        if chunk_strategy == "SLIDE_AWARE" and starts_new_slide_topic(page_elements, current):
            new_topic = True
        if new_topic and current_tokens() >= budgets["min_merge"]:
            flush()
        elif current_tokens() >= budgets["target"] or combined_tokens > budgets["max"]:
            flush()

        current.extend(page_elements)

    flush()
    return chunks


def page_has_content(elements: list[MarkdownElement]) -> bool:
    for element in elements:
        content = element.content.strip()
        if content and "No extractable content on page" not in content:
            return True
    return False


def starts_new_page_topic(elements: list[MarkdownElement]) -> bool:
    for element in elements:
        for line in element.content.splitlines():
            stripped = line.strip(" #\t")
            if not stripped or stripped.startswith("<!--"):
                continue
            return looks_like_topic_heading(stripped)
    return False


def looks_like_topic_heading(text: str) -> bool:
    if len(text) < 4 or len(text) > 80:
        return False
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False
    uppercase_ratio = sum(1 for char in letters if char.isupper()) / len(letters)
    heading_keywords = (
        "theorem",
        "proof",
        "example",
        "definition",
        "lemma",
        "corollary",
        "roadmap",
        "notes",
        "setup",
    )
    lowered = text.lower().rstrip(":")
    if any(lowered.startswith(keyword) for keyword in heading_keywords):
        return True
    return uppercase_ratio >= 0.72 and len(text.split()) <= 6


def split_large_page(elements: list[MarkdownElement], max_tokens: int = PAGE_AWARE_MAX_TOKENS) -> list[list[MarkdownElement]]:
    parts: list[list[MarkdownElement]] = []
    current: list[MarkdownElement] = []
    current_tokens = 0

    for element in elements:
        token_count = estimate_tokens(element.content)
        if current and current_tokens + token_count > max_tokens:
            parts.append(current)
            current = []
            current_tokens = 0
        current.append(element)
        current_tokens += token_count

    if current:
        parts.append(current)
    return parts


def build_question_aware_markdown_chunks(
    elements: list[MarkdownElement],
    layout_blocks: list[LayoutBlock],
    vlm_results: list[VlmResult],
    asset_ids_by_page: dict[int, str],
    strategy_context: dict,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current: list[MarkdownElement] = []
    budgets = token_budgets("QUESTION_AWARE")

    def current_tokens() -> int:
        return sum(estimate_tokens(element.content) for element in current)

    def flush() -> None:
        nonlocal current
        if not current:
            return
        chunks.append(chunk_from_elements(current, len(chunks), layout_blocks, vlm_results, asset_ids_by_page, strategy_context))
        current = []

    for element in elements:
        token_count = estimate_tokens(element.content)
        starts_question = starts_question_boundary(element)

        if should_markdown_element_be_standalone(element, token_count) and token_count >= budgets["target"]:
            flush()
            chunks.append(chunk_from_elements([element], len(chunks), layout_blocks, vlm_results, asset_ids_by_page, strategy_context))
            continue

        if current and starts_question and current_tokens() >= budgets["min_merge"]:
            flush()
        elif current and current_tokens() + token_count > budgets["max"]:
            flush()

        current.append(element)

    flush()
    return add_semantic_overlap(chunks)


def starts_new_slide_topic(page_elements: list[MarkdownElement], current: list[MarkdownElement]) -> bool:
    if not page_elements or not current:
        return False
    current_headings = [heading for element in current for heading in element.heading_path]
    next_headings = [heading for element in page_elements for heading in element.heading_path]
    if next_headings and current_headings and next_headings[-1] != current_headings[-1]:
        return True
    return starts_new_page_topic(page_elements)


def should_start_strategy_boundary(strategy: str, element: MarkdownElement, current: list[MarkdownElement]) -> bool:
    if not current:
        return False
    if strategy == "TOPIC_AWARE":
        return starts_academic_unit(element)
    if strategy == "PAPER_SECTION_AWARE":
        return starts_paper_section(element)
    return False


def starts_academic_unit(element: MarkdownElement) -> bool:
    text = first_semantic_line(element.content)
    if not text:
        return False
    lowered = text.lower().strip("#: ")
    prefixes = (
        "definition",
        "theorem",
        "lemma",
        "corollary",
        "proposition",
        "proof",
        "example",
        "solution",
        "remark",
    )
    return any(lowered.startswith(prefix) for prefix in prefixes)


def starts_paper_section(element: MarkdownElement) -> bool:
    if element.block_type != "HEADING":
        return False
    text = first_semantic_line(element.content).lower().strip("#: ")
    sections = (
        "abstract",
        "introduction",
        "background",
        "related work",
        "method",
        "methods",
        "methodology",
        "experiment",
        "experiments",
        "results",
        "discussion",
        "conclusion",
        "references",
    )
    return any(text.startswith(section) for section in sections)


def starts_question_boundary(element: MarkdownElement) -> bool:
    text = first_semantic_line(element.content).strip()
    if not text:
        return False
    patterns = (
        r"^#{1,6}\s*(question|problem|exercise)\s+\d+",
        r"^(question|problem|exercise)\s+\d+",
        r"^q\s*\d+[\).:\s]",
        r"^\d+\.\s+",
        r"^\d+\)\s+",
    )
    lowered = text.lower()
    return any(re.match(pattern, lowered) for pattern in patterns)


def first_semantic_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("<!--"):
            return stripped
    return ""


def chunk_from_elements(
    elements: list[MarkdownElement],
    chunk_index: int,
    layout_blocks: list[LayoutBlock],
    vlm_results: list[VlmResult],
    asset_ids_by_page: dict[int, str],
    strategy_context: Optional[dict] = None,
) -> TextChunk:
    page_start = min(el.page_number for el in elements)
    page_end = max(el.page_number for el in elements)
    chunk_type = dominant_markdown_type(elements)
    content = "\n\n".join(el.content for el in elements)
    
    headings = elements[0].heading_path if elements[0].heading_path else []
    block_types = sorted({el.block_type for el in elements})
    contains_image = "MIXED_VISUAL" in block_types
    contains_table = "TABLE" in block_types
    contains_formula = "FORMULA" in block_types
    contains_code = "CODE" in block_types
    
    bbox_refs = []
    asset_ids = set()
    
    for el in elements:
        bbox, asset_id = link_element_metadata(el, layout_blocks, vlm_results)
        if bbox:
            bbox_refs.append({
                "page": el.page_number,
                "bbox": bbox,
                "type": el.block_type
            })
        if asset_id:
            asset_ids.add(asset_id)
        else:
            page_asset_id = asset_ids_by_page.get(el.page_number)
            if page_asset_id:
                asset_ids.add(page_asset_id)
                
    sorted_asset_ids = sorted(list(asset_ids))
    
    metadata = {
        **(strategy_context or {}),
        "headings": headings,
        "blockTypes": block_types,
        "containsImage": contains_image,
        "containsTable": contains_table,
        "containsFormula": contains_formula,
        "containsCode": contains_code,
        "assetIds": sorted_asset_ids,
        "bboxRefs": bbox_refs,
    }
    
    return TextChunk(
        page_number=page_start,
        chunk_index=chunk_index,
        content=content,
        section_title=headings[0] if headings else None,
        page_start=page_start,
        page_end=page_end,
        chunk_type=chunk_type,
        token_count=estimate_tokens(content),
        source_asset_id=sorted_asset_ids[0] if len(sorted_asset_ids) == 1 else None,
        metadata_json=json.dumps(metadata, separators=(",", ":")),
    )
