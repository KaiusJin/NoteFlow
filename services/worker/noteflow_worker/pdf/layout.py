import json
import math
import re
from dataclasses import dataclass
from typing import Optional

import fitz

from noteflow_worker.db.repository import LayoutBlock, TextChunk, VisualRegion, VlmResult
from noteflow_worker.pdf.parser import (
    MAX_TOKENS,
    TARGET_TOKENS,
    classify_line,
    estimate_tokens,
    is_code_like,
    is_formula_like,
    preview_text,
)
from noteflow_worker.pdf.math_normalizer import balance_cases_environment, normalize_pdf_math_text
from noteflow_worker.pdf.code_normalizer import detect_code_language, normalize_code_source
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
    suppress_native_text_pages: set[int] | None = None,
    visual_regions: list[VisualRegion] | None = None,
) -> LayoutParseResult:
    suppress_native_text_pages = suppress_native_text_pages or set()
    visual_by_page = {page.page_number: page for page in visual_pages}
    vlm_by_page: dict[int, list[VlmResult]] = {}
    for result in vlm_results or []:
        vlm_by_page.setdefault(result.page_number, []).append(result)
    regions_by_page: dict[int, list[VisualRegion]] = {}
    for region in visual_regions or []:
        regions_by_page.setdefault(region.page_number, []).append(region)
    working_blocks: list[WorkingBlock] = []
    current_heading: Optional[str] = None
    heading_path: list[str] = []

    with fitz.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            page_blocks = [] if page_index in suppress_native_text_pages else extract_page_text_blocks(page, page_index)
            page_blocks = apply_vlm_formula_recovery(
                page_blocks,
                vlm_by_page.get(page_index, []),
                regions_by_page.get(page_index, []),
            )
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


def apply_vlm_formula_recovery(
    blocks: list[WorkingBlock],
    results: list[VlmResult],
    regions: list[VisualRegion],
) -> list[WorkingBlock]:
    """Replace only native formula blocks covered by successful formula crops."""
    region_by_key = {
        (region.page_number, region.region_index): region
        for region in regions
        if region.region_type == "FORMULA_IMAGE" and region.bbox_json
    }
    replacements: list[tuple[list[WorkingBlock], WorkingBlock]] = []
    already_replaced: set[int] = set()
    for result in results:
        if result.region_type != "FORMULA_IMAGE" or result.error_message or not result.latex.strip():
            continue
        region = region_by_key.get((result.page_number, result.region_index))
        if region is None:
            continue
        try:
            region_bbox = [float(value) for value in json.loads(region.bbox_json or "[]")]
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if len(region_bbox) != 4:
            continue
        covered = [
            block for block in blocks
            if id(block) not in already_replaced
            and block.block_type == "FORMULA"
            and block.bbox
            and bbox_coverage(block.bbox, region_bbox) >= 0.45
        ]
        if not covered:
            continue
        for block in covered:
            already_replaced.add(id(block))
        first = min(covered, key=lambda block: block.order)
        latex = normalize_vlm_formula_latex(result.latex)
        if not latex:
            continue
        replacement = WorkingBlock(
            page_number=first.page_number,
            order=first.order,
            block_type="FORMULA",
            content="$$\n" + latex + "\n$$",
            bbox=union_bbox([block.bbox for block in covered]) or region_bbox,
            section_title=first.section_title,
            heading_path=first.heading_path,
            source_asset_id=region.page_asset_id,
            confidence=0.92,
            metadata={
                **first.metadata,
                "source": "vlm_formula_layout_recovery",
                "regionIndex": result.region_index,
                "vlmProvider": result.provider,
                "vlmModel": result.model,
                "nativeBlockCountReplaced": len(covered),
            },
        )
        replacements.append((covered, replacement))
    if not replacements:
        return blocks
    removed = {id(block) for covered, _ in replacements for block in covered}
    output = [block for block in blocks if id(block) not in removed]
    output.extend(replacement for _, replacement in replacements)
    return sorted(output, key=lambda block: block.order)


def bbox_coverage(block_bbox: list[float], region_bbox: list[float]) -> float:
    intersection_width = max(0.0, min(block_bbox[2], region_bbox[2]) - max(block_bbox[0], region_bbox[0]))
    intersection_height = max(0.0, min(block_bbox[3], region_bbox[3]) - max(block_bbox[1], region_bbox[1]))
    block_area = max(1.0, (block_bbox[2] - block_bbox[0]) * (block_bbox[3] - block_bbox[1]))
    return intersection_width * intersection_height / block_area


def normalize_vlm_formula_latex(text: str) -> str:
    normalized = text.strip().replace("\\[", "").replace("\\]", "")
    normalized = normalized.replace("$$", "").strip()
    # A crop represents one display formula. Preserve explicit LaTeX row
    # separators and newlines; only collapse excessive blank lines that would
    # otherwise create invalid nested display blocks.
    parts = []
    for part in re.split(r"\s*---FORMULA---\s*", normalized):
        formula = part.strip()
        formula = re.sub(r"^\$|\$$", "", formula).strip()
        if formula:
            parts.append(formula)
    return "\n$$\n\n$$\n".join(parts)


def extract_page_text_blocks(page, page_number: int) -> list[WorkingBlock]:
    extracted: list[WorkingBlock] = []
    text_dict = page.get_text("dict", sort=True)
    for block_index, block in enumerate(text_dict.get("blocks", [])):
        if block.get("type") != 0:
            continue
        lines = block_lines(block)
        if not lines:
            continue
        source_text = "\n".join(lines)
        raw_text = normalize_pdf_math_text(source_text)
        normalized_lines = [line for line in raw_text.splitlines() if line.strip()]
        block_type = classify_text_block(normalized_lines)
        content = format_block_content(source_text if block_type == "CODE" else raw_text, block_type)
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
                    "pageWidth": float(page.rect.width),
                    "pageHeight": float(page.rect.height),
                },
            )
        )
    extracted = apply_multi_column_reading_order(extracted, float(page.rect.width))
    return merge_adjacent_small_text_blocks(extracted)


def apply_multi_column_reading_order(
    blocks: list[WorkingBlock],
    page_width: float,
) -> list[WorkingBlock]:
    """Order two-column body text by column while preserving wide separators."""
    boxed = [block for block in blocks if block.bbox]
    if len(boxed) < 4 or page_width <= 0:
        return blocks
    body_text = [
        block for block in boxed
        if block.block_type in {"PARAGRAPH", "LIST"}
        and block.token_count >= 10
        and is_prose_column_candidate(block.content)
        and (block.bbox[2] - block.bbox[0]) <= page_width * 0.52
    ]
    left = [block for block in body_text if (block.bbox[0] + block.bbox[2]) / 2 <= page_width * 0.42]
    right = [block for block in body_text if (block.bbox[0] + block.bbox[2]) / 2 >= page_width * 0.58]
    if not left or not right:
        return blocks
    if len(left) + len(right) < 4 and not (
        sum(block.token_count for block in left) >= 25
        and sum(block.token_count for block in right) >= 25
    ):
        return blocks

    left_span = (min(block.bbox[1] for block in left), max(block.bbox[3] for block in left))
    right_span = (min(block.bbox[1] for block in right), max(block.bbox[3] for block in right))
    overlap = max(0.0, min(left_span[1], right_span[1]) - max(left_span[0], right_span[0]))
    smaller_span = max(1.0, min(left_span[1] - left_span[0], right_span[1] - right_span[0]))
    if overlap / smaller_span < 0.35:
        return blocks

    wide = sorted(
        [block for block in boxed if (block.bbox[2] - block.bbox[0]) > page_width * 0.72],
        key=lambda block: block.bbox[1],
    )

    def reading_key(block: WorkingBlock) -> tuple[int, int, float, float]:
        if not block.bbox:
            return (999, 0, block.order[0], block.order[1])
        y0, x0 = block.bbox[1], block.bbox[0]
        if block in wide:
            position = wide.index(block)
            return (position * 2, 0, y0, x0)
        preceding_wide = sum(item.bbox[1] < y0 for item in wide)
        column = 0 if (block.bbox[0] + block.bbox[2]) / 2 < page_width / 2 else 1
        return (preceding_wide * 2 + 1, column, y0, x0)

    ordered = sorted(blocks, key=reading_key)
    return [
        WorkingBlock(
            page_number=block.page_number,
            order=(float(index), 0.0, block.order[2]),
            block_type=block.block_type,
            content=block.content,
            bbox=block.bbox,
            section_title=block.section_title,
            heading_path=block.heading_path,
            source_asset_id=block.source_asset_id,
            confidence=block.confidence,
            metadata={**block.metadata, "readingOrder": "two_column"},
        )
        for index, block in enumerate(ordered)
    ]


def is_prose_column_candidate(text: str) -> bool:
    words = re.findall(r"\S+", text)
    alphabetic = [word for word in words if re.search(r"[A-Za-z]{2}", word)]
    return len(alphabetic) >= 8 and len(alphabetic) / max(1, len(words)) >= 0.62


def block_lines(block: dict) -> list[str]:
    lines: list[str] = []
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        text = join_line_spans(spans).rstrip()
        if text.strip():
            lines.append(text)
    return lines


def join_line_spans(spans: list[dict]) -> str:
    """Join PDF glyph runs using their measured horizontal separation.

    PDF producers often omit an actual U+0020 between math and Roman fonts.
    Reconstructing a space from coordinates avoids vocabulary-destroying forms
    such as ``𝑡denotes`` without guessing particular words.
    """
    output = ""
    previous: dict | None = None
    for span in spans:
        value = str(span.get("text", ""))
        if not value:
            continue
        if previous is not None and output and not output[-1].isspace() and not value[0].isspace():
            previous_bbox = previous.get("bbox") or (0, 0, 0, 0)
            bbox = span.get("bbox") or (0, 0, 0, 0)
            gap = float(bbox[0]) - float(previous_bbox[2])
            reference_size = min(float(previous.get("size") or 10), float(span.get("size") or 10))
            if gap >= max(0.8, reference_size * 0.16):
                output += " "
        output += value
        previous = span
    return output


def classify_text_block(lines: list[str]) -> str:
    non_empty = [line.strip() for line in lines if line.strip()]
    if not non_empty:
        return "PARAGRAPH"
    line_types = [classify_line(line) for line in non_empty]
    if looks_like_sql_block(non_empty):
        return "CODE"
    majority = max(1, (len(non_empty) + 1) // 2)
    if line_types.count("CODE") >= majority:
        return "CODE"
    if line_types.count("FORMULA") >= majority and not is_prose_dominant_block(non_empty):
        return "FORMULA"
    if is_markdown_table_candidate(non_empty):
        return "TABLE"
    if len(non_empty) <= 2 and line_types[0] == "HEADING":
        return "HEADING"
    if line_types.count("LIST") >= max(1, len(non_empty) // 2):
        return "LIST"
    return most_common_text_type(line_types)


def is_prose_dominant_block(lines: list[str]) -> bool:
    text = " ".join(lines)
    words = re.findall(r"[A-Za-z]{2,}", text)
    tokens = re.findall(r"\S+", text)
    sentence_marks = len(re.findall(r"[.!?](?:\s|$)", text))
    return len(words) >= 8 and len(words) / max(1, len(tokens)) >= 0.48 and sentence_marks >= 1


def looks_like_sql_block(lines: list[str]) -> bool:
    joined = "\n".join(lines)
    starts_sql = bool(re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER)\b", joined, re.IGNORECASE))
    structural_keywords = len(
        re.findall(r"^\s*(FROM|WHERE|JOIN|GROUP\s+BY|ORDER\s+BY|HAVING|VALUES|SET)\b", joined, re.IGNORECASE | re.MULTILINE)
    )
    return starts_sql and structural_keywords >= 1


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
    if block_type == "CODE":
        code = normalize_code_source(text)
        language = detect_code_language(code)
        return f"```{language}\n{code}\n```"
    text = normalize_pdf_math_text(text)
    if "\\begin{cases}" in text or "\\end{cases}" in text:
        block_type = "FORMULA"
        text = normalize_cases_rows(balance_cases_environment(text))
    if block_type == "TABLE":
        return table_to_markdown(text)
    if block_type == "FORMULA":
        return "$$\n" + linearize_native_formula(text) + "\n$$"
    return text.strip()


def linearize_native_formula(text: str) -> str:
    """Produce a retrieval-safe fallback when native math is vertically split.

    VLM/geometry recovery can later replace this block with LaTeX. Until then,
    keeping fragments on one semantic line is substantially less destructive
    for tokenization than emitting a column of one-character Markdown lines.
    """
    if "\\begin{" in text:
        return text.strip()
    fragments = [fragment.strip() for fragment in text.splitlines() if fragment.strip()]
    return re.sub(r"\s+", " ", " ".join(fragments)).strip()


def normalize_cases_rows(text: str) -> str:
    if "\\begin{cases}" not in text:
        return text
    output: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "\\begin{cases}":
            inside = True
            output.append(stripped)
            continue
        if stripped == "\\end{cases}":
            inside = False
            output.append(stripped)
            continue
        if inside and stripped and not stripped.endswith("\\\\"):
            output.append(stripped + r" \\")
        else:
            output.append(line)
    return "\n".join(output)


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
        return page.ocr_text or ""
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
                    metadata={
                        "source": "merged_small_text_blocks",
                        "blockCount": len(buffer),
                        "pageWidth": first.metadata.get("pageWidth"),
                        "pageHeight": first.metadata.get("pageHeight"),
                    },
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
    total_pages = len({block.page_number for block in blocks})
    # Fewer than eight pages do not provide enough independent evidence for
    # automatic deletion. Candidates may still be annotated by later stages.
    minimum_pages = max(5, math.ceil(total_pages * 0.25))
    pages_by_exact: dict[str, set[int]] = {}
    pages_by_family: dict[str, set[int]] = {}
    texts_by_family: dict[str, set[str]] = {}
    pages_by_line_exact: dict[str, set[int]] = {}
    pages_by_line_family: dict[str, set[int]] = {}
    line_variants_by_family: dict[str, set[str]] = {}
    for block in blocks:
        if not is_noise_shape_candidate(block):
            continue
        exact = exact_noise_fingerprint(block.content)
        family = numeric_family_fingerprint(block.content)
        if not exact:
            continue
        pages_by_exact.setdefault(exact, set()).add(block.page_number)
        pages_by_family.setdefault(family, set()).add(block.page_number)
        texts_by_family.setdefault(family, set()).add(exact)
        for line in noise_lines(block.content):
            line_exact = exact_noise_fingerprint(line)
            line_family = numeric_family_fingerprint(line)
            pages_by_line_exact.setdefault(line_exact, set()).add(block.page_number)
            pages_by_line_family.setdefault(line_family, set()).add(block.page_number)
            line_variants_by_family.setdefault(line_family, set()).add(line_exact)

    marked: list[WorkingBlock] = []
    for block in blocks:
        decision = assess_noise_candidate(
            block,
            total_pages=total_pages,
            minimum_pages=minimum_pages,
            exact_pages=pages_by_exact.get(exact_noise_fingerprint(block.content), set()),
            family_pages=pages_by_family.get(numeric_family_fingerprint(block.content), set()),
            family_variants=texts_by_family.get(numeric_family_fingerprint(block.content), set()),
            repeated_line_evidence=repeated_line_evidence(
                block.content,
                minimum_pages,
                pages_by_line_exact,
                pages_by_line_family,
                line_variants_by_family,
            ),
        )
        if decision["action"] == "keep" and not decision["reasons"]:
            marked.append(block)
            continue
        marked.append(
            WorkingBlock(
                page_number=block.page_number,
                order=block.order,
                block_type="BOILERPLATE" if decision["action"] == "exclude" else block.block_type,
                content=block.content,
                bbox=block.bbox,
                section_title=block.section_title,
                heading_path=block.heading_path,
                source_asset_id=block.source_asset_id,
                confidence=block.confidence,
                metadata={
                    **block.metadata,
                    "noiseAssessment": {
                        "action": decision["action"],
                        "score": decision["score"],
                        "reasons": decision["reasons"],
                        "protected": decision["protected"],
                    },
                    "excludedFromChunks": decision["action"] == "exclude",
                },
            )
        )
    return marked


def is_noise_shape_candidate(block: WorkingBlock) -> bool:
    if block.source_asset_id:
        return False
    if block.block_type != "PARAGRAPH":
        return False
    if block.token_count > 28:
        return False
    text = block.content.strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 3:
        return False
    if not block.bbox:
        return False
    page_height = float(block.metadata.get("pageHeight") or 0.0)
    if page_height <= 0:
        return False
    return block.bbox[1] <= page_height * 0.12 or block.bbox[3] >= page_height * 0.88


def assess_noise_candidate(
    block: WorkingBlock,
    *,
    total_pages: int,
    minimum_pages: int,
    exact_pages: set[int],
    family_pages: set[int],
    family_variants: set[str],
    repeated_line_evidence: dict,
) -> dict:
    if total_pages < 8 or not is_noise_shape_candidate(block):
        return {"action": "keep", "score": 0.0, "reasons": [], "protected": False}
    protected_reasons = semantic_protection_reasons(block)
    if protected_reasons:
        return {
            "action": "keep",
            "score": 0.0,
            "reasons": ["semantic_content_protected", *protected_reasons],
            "protected": True,
        }

    score = 0.30  # edge-position evidence; never sufficient on its own
    reasons = ["repeated_edge_candidate"]
    exact_ratio = len(exact_pages) / max(1, total_pages)
    family_ratio = len(family_pages) / max(1, total_pages)
    if len(exact_pages) >= minimum_pages:
        score += 0.50
        reasons.append("exact_cross_page_repetition")
    elif len(family_pages) >= minimum_pages:
        score += 0.32
        reasons.append("numeric_family_repetition")
        if len(family_variants) >= 3:
            score += 0.12
            reasons.append("multiple_numeric_variants")
    if repeated_line_evidence["coverage"] >= 0.66:
        score += 0.46
        reasons.append("majority_lines_repeat_across_pages")
        if repeated_line_evidence["numericVariantLines"]:
            score += 0.08
            reasons.append("repeated_lines_have_numeric_variants")
    if block.token_count <= 12:
        score += 0.08
        reasons.append("short_low_context_text")
    if exact_ratio >= 0.65 or family_ratio >= 0.75:
        score += 0.08
        reasons.append("dominant_document_repetition")
    score = round(min(1.0, score), 3)
    if score >= 0.84:
        action = "exclude"
    elif score >= 0.62:
        action = "annotate"
    else:
        action = "keep"
    return {"action": action, "score": score, "reasons": reasons, "protected": False}


def semantic_protection_reasons(block: WorkingBlock) -> list[str]:
    if block.block_type in {"FORMULA", "CODE", "TABLE", "HEADING", "LIST"}:
        return [f"block_type={block.block_type.lower()}"]
    text = block.content.strip()
    reasons: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(is_formula_like(line) for line in lines):
        reasons.append("formula_like_content")
    if any(is_code_like(line) for line in lines) or "```" in text:
        reasons.append("code_like_content")
    if any(token in text for token in ("\\frac", "\\sum", "\\int", "\\begin{", "∑", "∫", "≤", "≥")):
        reasons.append("explicit_math_notation")
    symbols = sum(char in "=+−-*/^_{}[]()<>|∑∫√∞≤≥" for char in text)
    non_space = sum(not char.isspace() for char in text)
    if non_space and symbols / non_space >= 0.12:
        reasons.append("high_symbol_density")
    if re.search(r"\b(class|struct|def|function|return|import|include)\b", text, re.IGNORECASE):
        reasons.append("programming_language_signal")
    return list(dict.fromkeys(reasons))


def noise_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def repeated_line_evidence(
    text: str,
    minimum_pages: int,
    pages_by_exact: dict[str, set[int]],
    pages_by_family: dict[str, set[int]],
    variants_by_family: dict[str, set[str]],
) -> dict:
    lines = noise_lines(text)
    if not lines:
        return {"coverage": 0.0, "numericVariantLines": 0}
    repeated = 0
    numeric_variants = 0
    for line in lines:
        exact = exact_noise_fingerprint(line)
        family = numeric_family_fingerprint(line)
        if len(pages_by_exact.get(exact, set())) >= minimum_pages:
            repeated += 1
            continue
        if len(pages_by_family.get(family, set())) >= minimum_pages:
            repeated += 1
            if len(variants_by_family.get(family, set())) >= 3:
                numeric_variants += 1
    return {
        "coverage": repeated / len(lines),
        "numericVariantLines": numeric_variants,
    }


def exact_noise_fingerprint(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def numeric_family_fingerprint(text: str) -> str:
    # Numeric normalization is deliberately weak evidence and is never allowed
    # to override semantic formula/code protection.
    return re.sub(r"\d+", "#", exact_noise_fingerprint(text))


def layout_boilerplate_fingerprint(text: str) -> str:
    """Compatibility alias for scripts; not an exclusion decision by itself."""
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
    has_linked_visual_asset = False
    
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
            has_linked_visual_asset = True
        else:
            page_asset_id = asset_ids_by_page.get(el.page_number)
            if page_asset_id:
                asset_ids.add(page_asset_id)

    contains_image = contains_image or has_linked_visual_asset
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
