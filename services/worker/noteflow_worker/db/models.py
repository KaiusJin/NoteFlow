from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class DocumentRecord:
    id: str
    storage_path: str
    document_type: str
    user_id: str = ""
    title: str = ""
    content_source_type: str = "UNKNOWN"
    page_count: Optional[int] = None


@dataclass(frozen=True)
class TextChunk:
    page_number: int
    chunk_index: int
    content: str
    section_title: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    chunk_type: str = "PARAGRAPH"
    token_count: Optional[int] = None
    source_asset_id: Optional[str] = None
    metadata_json: Optional[str] = None
    id: Optional[str] = None


@dataclass(frozen=True)
class PageAsset:
    document_id: str
    page_number: int
    asset_type: str
    image_path: str
    width: int
    height: int
    image_count: int
    drawing_count: int
    image_coverage: float
    text_length: int
    visual_summary: Optional[str] = None


@dataclass(frozen=True)
class LayoutBlock:
    document_id: str
    page_number: int
    block_index: int
    block_type: str
    content: str
    bbox_json: Optional[str] = None
    section_title: Optional[str] = None
    heading_path_json: Optional[str] = None
    source_asset_id: Optional[str] = None
    confidence: Optional[float] = None
    metadata_json: Optional[str] = None


@dataclass(frozen=True)
class VisualRegion:
    document_id: str
    page_number: int
    region_index: int
    region_type: str
    asset_path: str
    bbox_json: Optional[str]
    page_asset_id: Optional[str]
    width: int
    height: int
    confidence: float
    metadata_json: Optional[str] = None


@dataclass(frozen=True)
class VlmResult:
    document_id: str
    page_number: int
    region_index: int
    region_type: str
    provider: str
    model: str
    transcription: str
    description: str
    latex: str
    code: str
    uncertainty: str
    search_text: str
    raw_response_json: Optional[str] = None
    error_message: Optional[str] = None
    input_fingerprint: Optional[str] = None
    attempt_count: int = 1
    content_kind: str = "unknown"
    importance: str = "medium"
    reading_order: str = ""
    language: str = "unknown"


@dataclass(frozen=True)
class MarkdownPage:
    document_id: str
    page_number: int
    markdown: str
    source_type: str
    quality_score: float
    warnings_json: Optional[str] = None
    structure_json: Optional[str] = None


@dataclass(frozen=True)
class MarkdownDocument:
    document_id: str
    markdown: str
    structure_json: Optional[str]
    quality_report_json: Optional[str]


@dataclass(frozen=True)
class AiNoteSection:
    note_id: str
    document_id: str
    section_index: int
    section_type: str
    heading: str
    markdown: str
    page_start: Optional[int]
    page_end: Optional[int]
    source_chunk_ids_json: str
    source_pages_json: str
    confidence: float
    warnings_json: str
    metadata_json: Optional[str] = None
    id: Optional[str] = None


@dataclass(frozen=True)
class EmbeddingSource:
    document_id: str
    source_domain: str
    source_object_type: str
    source_object_id: str
    embedding_text: str
    text_preview: str
    metadata_json: Optional[str] = None


@dataclass(frozen=True)
class DocumentEmbedding:
    document_id: str
    source_domain: str
    source_object_type: str
    source_object_id: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    content_hash: str
    embedding_text: str
    text_preview: str
    embedding: list[float]
    metadata_json: Optional[str] = None
