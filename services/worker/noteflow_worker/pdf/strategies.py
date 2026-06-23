from dataclasses import dataclass


HANDWRITTEN_DOCUMENT_TYPE = "HANDWRITTEN_NOTES"
PAGE_VISUAL_SOURCE_TYPES = {"SCANNED_PDF", "HANDWRITTEN_SCAN"}


@dataclass(frozen=True)
class ProcessingStrategy:
    document_type: str
    content_source_type: str
    markdown_strategy: str
    chunk_strategy: str
    force_full_page_vlm: bool
    require_vlm_success: bool


def resolve_processing_strategy(document_type: str | None, content_source_type: str | None) -> ProcessingStrategy:
    normalized_document_type = document_type or "OTHER"
    normalized_source_type = content_source_type or "UNKNOWN"

    if normalized_document_type == HANDWRITTEN_DOCUMENT_TYPE:
        return ProcessingStrategy(
            document_type=normalized_document_type,
            content_source_type=normalized_source_type,
            markdown_strategy="FULL_PAGE_VLM",
            chunk_strategy="PAGE_AWARE",
            force_full_page_vlm=True,
            require_vlm_success=True,
        )

    if normalized_source_type in PAGE_VISUAL_SOURCE_TYPES:
        return ProcessingStrategy(
            document_type=normalized_document_type,
            content_source_type=normalized_source_type,
            markdown_strategy="PAGE_LEVEL_VISUAL",
            chunk_strategy="PAGE_AWARE",
            force_full_page_vlm=True,
            require_vlm_success=True,
        )

    if normalized_document_type == "LECTURE_SLIDES":
        return ProcessingStrategy(
            document_type=normalized_document_type,
            content_source_type=normalized_source_type,
            markdown_strategy="SLIDE_LAYOUT",
            chunk_strategy="SLIDE_AWARE",
            force_full_page_vlm=False,
            require_vlm_success=False,
        )

    if normalized_document_type == "COURSE_NOTES":
        return ProcessingStrategy(
            document_type=normalized_document_type,
            content_source_type=normalized_source_type,
            markdown_strategy="STRUCTURAL_NOTES",
            chunk_strategy="TOPIC_AWARE",
            force_full_page_vlm=False,
            require_vlm_success=False,
        )

    if normalized_document_type == "RESEARCH_PAPER":
        return ProcessingStrategy(
            document_type=normalized_document_type,
            content_source_type=normalized_source_type,
            markdown_strategy="PAPER_SECTIONS",
            chunk_strategy="PAPER_SECTION_AWARE",
            force_full_page_vlm=False,
            require_vlm_success=False,
        )

    if normalized_document_type in {"ASSIGNMENT", "PAST_EXAM"}:
        return ProcessingStrategy(
            document_type=normalized_document_type,
            content_source_type=normalized_source_type,
            markdown_strategy="QUESTION_STRUCTURE",
            chunk_strategy="QUESTION_AWARE",
            force_full_page_vlm=False,
            require_vlm_success=False,
        )

    return ProcessingStrategy(
        document_type=normalized_document_type,
        content_source_type=normalized_source_type,
        markdown_strategy="MIXED_LAYOUT",
        chunk_strategy="MIXED_FALLBACK",
        force_full_page_vlm=False,
        require_vlm_success=False,
    )
