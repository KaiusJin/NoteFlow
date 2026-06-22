import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from noteflow_worker.db.repository import LayoutBlock, MarkdownDocument, MarkdownPage, VlmResult
from noteflow_worker.pdf.parser import estimate_tokens


TEXT_BLOCK_TYPES = {"PARAGRAPH", "HEADING", "LIST", "CODE", "FORMULA", "TABLE"}
VISUAL_BLOCK_TYPES = {"IMAGE", "MIXED_VISUAL"}


@dataclass(frozen=True)
class MarkdownBuildResult:
    pages: list[MarkdownPage]
    document: MarkdownDocument


@dataclass(frozen=True)
class RenderedVisual:
    markdown: str
    visual_type: str
    source: str
    normalized: str
    warning: str | None = None


def build_markdown_document(
    document_id: str,
    layout_blocks: list[LayoutBlock],
    vlm_results: list[VlmResult],
) -> MarkdownBuildResult:
    blocks_by_page: dict[int, list[LayoutBlock]] = {}
    for block in layout_blocks:
        if block.block_type == "BOILERPLATE":
            continue
        blocks_by_page.setdefault(block.page_number, []).append(block)

    vlm_by_page: dict[int, list[VlmResult]] = {}
    for result in vlm_results:
        if result.error_message:
            continue
        vlm_by_page.setdefault(result.page_number, []).append(result)

    page_numbers = sorted(set(blocks_by_page) | set(vlm_by_page))
    pages: list[MarkdownPage] = []
    document_parts: list[str] = []
    headings: list[dict] = []
    quality_scores: list[float] = []
    warning_counts: dict[str, int] = {}

    for page_number in page_numbers:
        page = build_markdown_page(
            document_id,
            page_number,
            sorted(blocks_by_page.get(page_number, []), key=lambda block: block.block_index),
            sorted(vlm_by_page.get(page_number, []), key=lambda result: result.region_index),
        )
        pages.append(page)
        quality_scores.append(page.quality_score)
        document_parts.append(f"<!-- page:{page.page_number} -->\n\n{page.markdown}")
        page_structure = json.loads(page.structure_json or "{}")
        headings.extend(
            {"page": page.page_number, "text": heading}
            for heading in page_structure.get("headings", [])
        )
        for warning in json.loads(page.warnings_json or "[]"):
            warning_counts[warning] = warning_counts.get(warning, 0) + 1

    markdown = "\n\n---\n\n".join(part for part in document_parts if part.strip())
    quality_report = {
        "pageCount": len(pages),
        "averageQualityScore": round(sum(quality_scores) / len(quality_scores), 3) if quality_scores else 0.0,
        "warningCounts": warning_counts,
        "estimatedTokens": estimate_tokens(markdown),
    }
    document = MarkdownDocument(
        document_id=document_id,
        markdown=markdown,
        structure_json=json.dumps({"headings": headings}, separators=(",", ":")),
        quality_report_json=json.dumps(quality_report, separators=(",", ":")),
    )
    return MarkdownBuildResult(pages=pages, document=document)


def build_markdown_page(
    document_id: str,
    page_number: int,
    blocks: list[LayoutBlock],
    vlm_results: list[VlmResult],
) -> MarkdownPage:
    text_parts: list[str] = []
    headings: list[str] = []
    block_types: list[str] = []

    for block in blocks:
        if block.block_type in VISUAL_BLOCK_TYPES:
            continue
        if block.block_type not in TEXT_BLOCK_TYPES:
            continue
        rendered = render_text_block(block)
        if not rendered:
            continue
        text_parts.append(rendered)
        block_types.append(block.block_type)
        if block.block_type == "HEADING":
            headings.append(strip_markdown(rendered))

    page_text = "\n\n".join(text_parts)
    text_fingerprint = normalize_for_similarity(page_text)
    rendered_visuals = render_visuals(vlm_results, text_fingerprint)

    warnings: list[str] = []
    visual_parts: list[str] = []
    seen: list[str] = []
    visual_types: list[str] = []
    filtered_count = 0
    for visual in rendered_visuals:
        if not visual.markdown:
            filtered_count += 1
            if visual.warning:
                warnings.append(visual.warning)
            continue
        if is_duplicate(visual.normalized, seen):
            filtered_count += 1
            warnings.append("duplicate_visual_region_filtered")
            continue
        seen.append(visual.normalized)
        visual_parts.append(visual.markdown)
        visual_types.append(visual.visual_type)
        if visual.warning:
            warnings.append(visual.warning)

    markdown_parts = [part for part in [page_text, *visual_parts] if part.strip()]
    markdown = "\n\n".join(markdown_parts).strip()
    if not markdown:
        warnings.append("empty_markdown_page")
        markdown = f"<!-- No extractable content on page {page_number}. -->"

    source_type = infer_page_source_type(block_types, visual_types)
    quality_score = score_page_quality(markdown, warnings, filtered_count)
    structure = {
        "headings": headings,
        "blockTypes": sorted(set(block_types)),
        "visualTypes": sorted(set(visual_types)),
        "filteredVisualRegions": filtered_count,
        "estimatedTokens": estimate_tokens(markdown),
    }
    return MarkdownPage(
        document_id=document_id,
        page_number=page_number,
        markdown=markdown,
        source_type=source_type,
        quality_score=quality_score,
        warnings_json=json.dumps(sorted(set(warnings)), separators=(",", ":")),
        structure_json=json.dumps(structure, separators=(",", ":")),
    )


def render_text_block(block: LayoutBlock) -> str:
    content = normalize_markdown_spacing(block.content or "")
    if not content:
        return ""
    if block.block_type == "HEADING":
        heading = strip_markdown(content)
        if not heading:
            return ""
        return f"## {heading}"
    return content


def render_visuals(results: list[VlmResult], page_text_fingerprint: str) -> list[RenderedVisual]:
    rendered: list[RenderedVisual] = []
    for result in results:
        visual_type = classify_visual_result(result)
        if visual_type == "DECORATIVE_IMAGE":
            rendered.append(RenderedVisual("", visual_type, "vlm", "", "decorative_visual_filtered"))
            continue
        if (
            result.region_type in {"FULL_PAGE_VISUAL", "HANDWRITTEN"}
            and page_text_fingerprint
        ):
            rendered.append(RenderedVisual("", visual_type, "vlm", "", "full_page_visual_duplicate_of_text"))
            continue
        markdown = render_visual_result(result, visual_type)
        normalized = normalize_for_similarity(markdown)
        if not normalized:
            rendered.append(RenderedVisual("", visual_type, "vlm", "", "empty_visual_region_filtered"))
            continue
        if visual_type in {"TEXT_IMAGE", "FORMULA_IMAGE", "CODE_IMAGE", "TABLE_IMAGE"} and is_text_already_present(
            normalized,
            page_text_fingerprint,
        ):
            rendered.append(RenderedVisual("", visual_type, "vlm", normalized, "visual_text_duplicate_of_pdf_text"))
            continue
        rendered.append(RenderedVisual(markdown, visual_type, "vlm", normalized))
    return rendered


def classify_visual_result(result: VlmResult) -> str:
    text = "\n".join(
        item
        for item in [result.transcription, result.description, result.latex, result.code, result.search_text]
        if item
    )
    normalized = text.lower()
    transcription = result.transcription.strip()
    if not transcription and is_decorative_text(normalized):
        return "DECORATIVE_IMAGE"
    if looks_like_code(result.code) or looks_like_code(result.transcription):
        return "CODE_IMAGE"
    if result.latex.strip() or looks_like_formula(result.transcription):
        return "FORMULA_IMAGE"
    if looks_like_table(result.transcription):
        return "TABLE_IMAGE"
    if looks_like_diagram(result.description.lower()):
        return "DIAGRAM"
    if result.transcription.strip():
        return "TEXT_IMAGE"
    if result.description.strip():
        return "DIAGRAM"
    return "UNKNOWN_VISUAL"


def render_visual_result(result: VlmResult, visual_type: str) -> str:
    transcription = normalize_markdown_spacing(result.transcription)
    description = normalize_markdown_spacing(result.description)
    latex = normalize_latex(result.latex)
    code = normalize_code(result.code or result.transcription)

    # If the region represents a full page visual fallback or a handwritten note,
    # the transcription is the entire content of the page. We must return it.
    if result.region_type in {"FULL_PAGE_VISUAL", "HANDWRITTEN"} or len(transcription.split()) > 12:
        if visual_type in {"DIAGRAM", "UNKNOWN_VISUAL"} and description:
            return transcription + f"\n\n*Figure Explanation: {description}*"
        return transcription

    if visual_type == "TEXT_IMAGE":
        return transcription
    if visual_type == "FORMULA_IMAGE":
        formula = latex or normalize_latex(transcription)
        return "$$\n" + formula + "\n$$" if formula else transcription
    if visual_type == "CODE_IMAGE":
        return "```c\n" + code + "\n```" if code else transcription
    if visual_type == "TABLE_IMAGE":
        return table_text_to_markdown(transcription)
    if visual_type in {"DIAGRAM", "UNKNOWN_VISUAL"}:
        title = visual_type.replace("_", " ").title()
        parts = [f"<figure data-page=\"{result.page_number}\" data-region=\"{result.region_index}\" data-type=\"{visual_type.lower()}\">"]
        parts.append(f"\n**{title}**")
        if transcription:
            parts.append("\nVisible text:\n" + transcription)
        if description:
            parts.append("\nExplanation:\n" + description)
        if latex:
            parts.append("\nLaTeX:\n$$\n" + latex + "\n$$")
        if result.uncertainty:
            parts.append("\nUncertainty:\n" + normalize_markdown_spacing(result.uncertainty))
        parts.append("\n</figure>")
        return "\n".join(parts)
    return ""


def infer_page_source_type(block_types: list[str], visual_types: list[str]) -> str:
    has_text = bool(block_types)
    has_visual = bool(visual_types)
    if has_text and has_visual:
        return "hybrid"
    if has_visual:
        return "vlm"
    if has_text:
        return "text"
    return "unknown"


def score_page_quality(markdown: str, warnings: list[str], filtered_count: int) -> float:
    score = 1.0
    if estimate_tokens(markdown) < 20:
        score -= 0.2
    score -= min(0.35, len(set(warnings)) * 0.07)
    if filtered_count:
        score -= min(0.15, filtered_count * 0.02)
    return round(max(0.0, score), 3)


def is_decorative_text(text: str) -> bool:
    if not text.strip():
        return True
    decorative_terms = (
        "wooden floor",
        "wood planks",
        "wood grain",
        "background",
        "texture",
        "decorative",
        "floorboards",
    )
    if any(term in text for term in decorative_terms) and not re.search(r"\b(theorem|example|definition|code|array|pointer|series|function)\b", text):
        return True
    words = re.findall(r"[a-zA-Z0-9_]+", text)
    return len(words) <= 2 and not re.search(r"[=∑∞{}();]", text)


def looks_like_code(text: str) -> bool:
    if not text:
        return False
    code_terms = ("#include", "return ", "printf", "malloc", "int ", "char ", "void ", "struct ", "assert(")
    if any(term in text for term in code_terms):
        return True
    return bool(re.search(r"[{};]\s*$", text, re.MULTILINE)) and bool(re.search(r"\b[a-zA-Z_][a-zA-Z0-9_]*\s*[=(]", text))


def looks_like_formula(text: str) -> bool:
    if not text:
        return False
    formula_tokens = ("\\sum", "\\frac", "\\int", "∑", "∞", "≤", "≥", "lim", "_{", "^")
    if any(token in text for token in formula_tokens):
        return True
    symbol_count = sum(1 for char in text if char in "=+-*/^()[]{}|")
    return symbol_count >= 5 and len(text.split()) <= 45 and not looks_like_code(text)


def looks_like_table(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    if "|" in text and text.count("|") >= 4:
        return True
    return sum(1 for line in lines if len(re.split(r"\s{2,}", line.strip())) >= 2) >= 2


def looks_like_diagram(text: str) -> bool:
    diagram_terms = (
        "diagram",
        "chart",
        "graph",
        "pointer",
        "array",
        "stack",
        "heap",
        "arrow",
        "address",
        "flow",
        "plot",
        "axis",
        "label",
    )
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in diagram_terms)


def is_text_already_present(visual_text: str, page_text: str) -> bool:
    if not visual_text or not page_text:
        return False
    visual_tokens = set(visual_text.split())
    page_tokens = set(page_text.split())
    meaningful_visual_tokens = {token for token in visual_tokens if len(token) >= 3}
    meaningful_page_tokens = {token for token in page_tokens if len(token) >= 3}
    if meaningful_visual_tokens and meaningful_page_tokens:
        overlap = len(meaningful_visual_tokens & meaningful_page_tokens)
        if overlap / max(1, min(len(meaningful_visual_tokens), len(meaningful_page_tokens))) >= 0.7:
            return True
    if len(visual_text) < 40:
        return visual_text in page_text
    if visual_text in page_text:
        return True
    return SequenceMatcher(None, visual_text[:1200], page_text[:3000]).ratio() >= 0.82


def is_duplicate(candidate: str, seen: list[str]) -> bool:
    if not candidate:
        return True
    for item in seen:
        if candidate in item or item in candidate:
            return True
        if SequenceMatcher(None, candidate[:1200], item[:1200]).ratio() >= 0.82:
            return True
    return False


def normalize_for_similarity(text: str) -> str:
    text = strip_markdown(text).lower()
    text = re.sub(r"[^a-z0-9_+\-*/=<>≤≥∑∞]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_markdown_spacing(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                compact.append("")
            blank = True
            continue
        compact.append(line)
        blank = False
    return "\n".join(compact).strip()


def strip_markdown(text: str) -> str:
    text = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", text).replace("```", "")
    text = text.replace("$$", "")
    text = re.sub(r"^#+\s*", "", text.strip())
    return normalize_markdown_spacing(text)


def normalize_latex(text: str) -> str:
    text = normalize_markdown_spacing(text)
    text = text.replace("$$", "").strip()
    return text


def normalize_code(text: str) -> str:
    text = normalize_markdown_spacing(text)
    text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def table_text_to_markdown(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rows = [split_table_line(line) for line in lines]
    rows = [row for row in rows if len(row) >= 2]
    if not rows:
        return normalize_markdown_spacing(text)
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    body = rows[1:]
    return "\n".join(
        ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
        + ["| " + " | ".join(row) + " |" for row in body]
    )


def split_table_line(line: str) -> list[str]:
    if "|" in line:
        return [cell.strip() for cell in line.strip("|").split("|") if cell.strip()]
    return [cell.strip() for cell in re.split(r"\s{2,}", line.strip()) if cell.strip()]
