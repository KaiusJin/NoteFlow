import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

from noteflow_worker.db.repository import TextChunk


MIN_TOKENS = 80
TARGET_TOKENS = 450
MAX_TOKENS = 800
MAX_PAGE_SPAN = 3


@dataclass(frozen=True)
class ParsedPdf:
    page_count: int
    text: str
    preview: str
    content_source_type: str
    chunks: list[TextChunk]


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class TextLine:
    page_number: int
    line_index: int
    total_lines: int
    text: str
    line_type: str
    normalized: str
    is_boilerplate: bool = False

    @property
    def edge_position(self) -> bool:
        if self.total_lines <= 1:
            return True
        ratio = self.line_index / max(1, self.total_lines - 1)
        return ratio <= 0.18 or ratio >= 0.82


@dataclass(frozen=True)
class TextBlock:
    page_start: int
    page_end: int
    block_type: str
    text: str
    section_title: Optional[str]

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.text)


def parse_pdf(path: str, document_type: str) -> ParsedPdf:
    reader = PdfReader(path)
    pages = extract_pages(reader)
    full_text = "\n\n".join(page.text for page in pages if page.text)
    source_type = detect_content_source_type(
        page_count=len(reader.pages),
        extracted_text_length=len(full_text),
        document_type=document_type,
    )

    chunks: list[TextChunk] = []
    if source_type not in {"SCANNED_PDF", "HANDWRITTEN_SCAN"}:
        lines = classify_lines(pages)
        lines = mark_repeated_boilerplate(lines)
        blocks = build_blocks(lines)
        chunks = build_chunks(blocks)
    preview_source = "\n\n".join(chunk.content for chunk in chunks[:3]) if chunks else full_text

    return ParsedPdf(
        page_count=len(reader.pages),
        text=full_text,
        preview=preview_text(preview_source),
        content_source_type=source_type,
        chunks=chunks,
    )


def extract_pages(reader: PdfReader) -> list[PageText]:
    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        extracted = page.extract_text() or ""
        normalized = normalize_page_text(extracted)
        if normalized:
            pages.append(PageText(page_number=index, text=normalized))
    return pages


def normalize_page_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def preview_text(text: str, max_chars: int = 600) -> str:
    compact = " ".join(text.split())
    return compact[:max_chars]


def classify_lines(pages: list[PageText]) -> list[TextLine]:
    lines: list[TextLine] = []
    for page in pages:
        page_lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        total = len(page_lines)
        for index, line in enumerate(page_lines):
            line_type = classify_line(line)
            lines.append(
                TextLine(
                    page_number=page.page_number,
                    line_index=index,
                    total_lines=total,
                    text=line,
                    line_type=line_type,
                    normalized=normalize_for_repetition(line),
                )
            )
    return lines


def classify_line(line: str) -> str:
    stripped = line.strip()
    if is_code_like(stripped):
        return "CODE"
    if is_formula_like(stripped):
        return "FORMULA"
    if is_table_like(stripped):
        return "TABLE"
    if is_list_item(stripped):
        return "LIST"
    if is_heading_like(stripped):
        return "HEADING"
    return "PARAGRAPH"


def is_code_like(line: str) -> bool:
    if line in {"{", "}", "};"}:
        return True
    if re.match(r"^\s*(class|def|struct)\s+[A-Za-z_][A-Za-z0-9_]*", line):
        return True
    code_tokens = (
        "#include",
        ";;",
        "return ",
        "malloc",
        "printf",
        "->",
        "};",
        "){",
        ") {",
        "//",
        "/*",
        "*/",
    )
    if any(token in line for token in code_tokens):
        return True
    if line.endswith(";") and re.search(r"\b(int|void|char|float|double|bool|size_t|const)\b", line):
        return True
    symbol_count = sum(1 for char in line if char in "{}[]();=*&|<>")
    return len(line) <= 120 and symbol_count >= 5 and bool(re.search(r"[A-Za-z_]", line))


def is_formula_like(line: str) -> bool:
    math_tokens = (
        "\\sum",
        "\\int",
        "\\frac",
        "\\operatorname",
        "E[",
        "Var(",
        "P(",
        "≤",
        "≥",
        "∑",
        "∫",
        "θ",
        "α",
        "β",
        "μ",
        "σ",
    )
    if any(token in line for token in math_tokens):
        return True
    has_operator = bool(re.search(r"[=^]|[A-Za-z]\s*/\s*[A-Za-z0-9]", line))
    compact_expression = len(line.split()) <= 16 and bool(re.search(r"[A-Za-z0-9\]\)]", line))
    return has_operator and compact_expression and not is_code_like(line)


def is_table_like(line: str) -> bool:
    if len(re.findall(r"\bO\([^)]+\)", line)) >= 2:
        return True
    if "|" in line and line.count("|") >= 2:
        return True
    columns = re.split(r"\s{2,}", line)
    return len(columns) >= 3 and len(line) <= 180


def is_list_item(line: str) -> bool:
    return bool(re.match(r"^([-*•]|\d+[.)])\s+\S+", line))


def is_heading_like(line: str) -> bool:
    words = line.split()
    prompt_prefixes = ("hint:", "ex.", "exercise:", "what's", "what’s", "write a", "using ")
    if line.lower().startswith(prompt_prefixes):
        return False
    if len(line) > 90 or len(words) > 10:
        return False
    if line.endswith((".", ";", ",")):
        return False
    if re.match(r"^\d+[/\\]\d+\b", line):
        return False
    if is_formula_like(line) or is_code_like(line):
        return False
    if not re.search(r"[A-Za-z]", line):
        return False
    starts_like_title = bool(re.match(r"^[A-Z][A-Za-z0-9]", line))
    has_heading_punctuation = ":" in line
    if not starts_like_title:
        return False
    if has_heading_punctuation:
        return True
    if len(words) > 5:
        return False
    title_case_words = sum(1 for word in words if re.match(r"^[A-Z][a-z]+", word))
    lowercase_words = sum(1 for word in words if re.match(r"^[a-z]+", word))
    return title_case_words >= 1 and lowercase_words <= max(1, len(words) // 2)


def normalize_for_repetition(line: str) -> str:
    normalized = re.sub(r"\d+", "#", line.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def mark_repeated_boilerplate(lines: list[TextLine]) -> list[TextLine]:
    pages_by_pattern: dict[str, set[int]] = defaultdict(set)
    pages_by_family: dict[str, set[int]] = defaultdict(set)
    for line in lines:
        if can_be_boilerplate_candidate(line):
            pages_by_pattern[line.normalized].add(line.page_number)
            pages_by_family[boilerplate_family_key(line.normalized)].add(line.page_number)

    total_pages = len({line.page_number for line in lines})
    min_pages = max(3, math.ceil(total_pages * 0.18))
    min_family_pages = max(5, math.ceil(total_pages * 0.30))
    repeated_patterns = {
        pattern
        for pattern, pages in pages_by_pattern.items()
        if len(pages) >= min_pages
    }
    repeated_families = {
        family
        for family, pages in pages_by_family.items()
        if len(pages) >= min_family_pages
    }

    marked: list[TextLine] = []
    for line in lines:
        is_boilerplate = (
            (
                line.normalized in repeated_patterns
                or boilerplate_family_key(line.normalized) in repeated_families
            )
            and can_be_boilerplate_candidate(line)
        )
        marked.append(
            TextLine(
                page_number=line.page_number,
                line_index=line.line_index,
                total_lines=line.total_lines,
                text=line.text,
                line_type="BOILERPLATE" if is_boilerplate else line.line_type,
                normalized=line.normalized,
                is_boilerplate=is_boilerplate,
            )
        )
    return marked


def can_be_boilerplate_candidate(line: TextLine) -> bool:
    if not line.edge_position:
        return False
    if len(line.text) > 140:
        return False
    if line.line_type in {"CODE", "FORMULA", "TABLE"}:
        return False
    return True


def boilerplate_family_key(normalized: str) -> str:
    parts = normalized.split()
    if len(parts) <= 8:
        return normalized
    return " ".join(parts[:8])


def build_blocks(lines: list[TextLine]) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    current_section: Optional[str] = None
    buffer: list[TextLine] = []
    buffer_type: Optional[str] = None

    def flush() -> None:
        nonlocal buffer, buffer_type
        if not buffer or buffer_type is None:
            return
        text = "\n".join(line.text for line in buffer)
        blocks.append(
            TextBlock(
                page_start=min(line.page_number for line in buffer),
                page_end=max(line.page_number for line in buffer),
                block_type=buffer_type,
                text=text,
                section_title=current_section,
            )
        )
        buffer = []
        buffer_type = None

    for line in lines:
        if line.is_boilerplate:
            flush()
            continue

        if line.line_type == "HEADING":
            flush()
            current_section = line.text
            blocks.append(
                TextBlock(
                    page_start=line.page_number,
                    page_end=line.page_number,
                    block_type="HEADING",
                    text=line.text,
                    section_title=current_section,
                )
            )
            continue

        grouped_type = line.line_type
        if grouped_type in {"LIST"}:
            grouped_type = "PARAGRAPH"

        if buffer_type != grouped_type:
            flush()
            buffer_type = grouped_type
        buffer.append(line)

    flush()
    return blocks


def build_chunks(blocks: list[TextBlock]) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current: list[TextBlock] = []
    chunk_index = 0

    def current_tokens() -> int:
        return sum(block.token_count for block in current)

    def current_page_span_with(block: Optional[TextBlock] = None) -> int:
        candidates = current + ([block] if block else [])
        if not candidates:
            return 0
        return max(item.page_end for item in candidates) - min(item.page_start for item in candidates) + 1

    def flush(keep_overlap: bool = False) -> None:
        nonlocal current, chunk_index
        if not current:
            return
        chunks.append(chunk_from_blocks(current, chunk_index))
        chunk_index += 1
        current = overlap_blocks(current) if keep_overlap else []

    for block in blocks:
        if not current:
            current.append(block)
            if block.token_count > MAX_TOKENS:
                flush()
            continue

        section_changed = (
            block.section_title
            and current[-1].section_title
            and block.section_title != current[-1].section_title
            and current_tokens() >= MIN_TOKENS
        )
        too_many_tokens = current_tokens() + block.token_count > MAX_TOKENS
        enough_and_new_page = current_tokens() >= TARGET_TOKENS and block.page_start != current[-1].page_end
        too_many_pages = current_page_span_with(block) > MAX_PAGE_SPAN

        if section_changed or too_many_tokens or enough_and_new_page or too_many_pages:
            flush(keep_overlap=too_many_tokens or enough_and_new_page)
        current.append(block)

    if current:
        chunks.append(chunk_from_blocks(current, chunk_index))
    return dedupe_trailing_overlap(chunks)


def chunk_from_blocks(blocks: list[TextBlock], chunk_index: int) -> TextChunk:
    page_start = min(block.page_start for block in blocks)
    page_end = max(block.page_end for block in blocks)
    section_title = first_non_empty(block.section_title for block in blocks)
    chunk_type = dominant_chunk_type(blocks)
    content = "\n\n".join(block.text for block in blocks)
    return TextChunk(
        page_number=page_start,
        chunk_index=chunk_index,
        content=content,
        section_title=section_title,
        page_start=page_start,
        page_end=page_end,
        chunk_type=chunk_type,
        token_count=estimate_tokens(content),
    )


def overlap_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    overlap: list[TextBlock] = []
    total = 0
    for block in reversed(blocks):
        if block.block_type in {"CODE", "FORMULA"} and block.token_count > 120:
            continue
        overlap.insert(0, block)
        total += block.token_count
        if total >= 80 or len(overlap) >= 2:
            break
    return overlap


def dedupe_trailing_overlap(chunks: list[TextChunk]) -> list[TextChunk]:
    cleaned: list[TextChunk] = []
    seen: set[str] = set()
    for chunk in chunks:
        fingerprint = re.sub(r"\s+", " ", chunk.content).strip()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        cleaned.append(chunk)
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
        for index, chunk in enumerate(cleaned)
    ]


def dominant_chunk_type(blocks: list[TextBlock]) -> str:
    counts = Counter(block.block_type for block in blocks if block.block_type != "HEADING")
    if not counts:
        return "HEADING"
    return counts.most_common(1)[0][0]


def first_non_empty(values) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def estimate_tokens(text: str) -> int:
    word_estimate = int(len(text.split()) * 1.3)
    char_estimate = max(1, len(text) // 4)
    return max(word_estimate, char_estimate)


def detect_content_source_type(page_count: int, extracted_text_length: int, document_type: str) -> str:
    if page_count == 0:
        return "UNKNOWN"
    chars_per_page = extracted_text_length / page_count
    if document_type == "HANDWRITTEN_NOTES" and chars_per_page < 100:
        return "HANDWRITTEN_SCAN"
    if chars_per_page < 100:
        return "SCANNED_PDF"
    if chars_per_page < 300:
        return "MIXED"
    return "TEXT_PDF"


def ensure_pdf_exists(path: str) -> None:
    if not Path(path).exists():
        raise FileNotFoundError(f"PDF not found: {path}")
