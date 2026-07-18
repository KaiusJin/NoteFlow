import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from noteflow_worker.config import settings


@dataclass(frozen=True)
class DocumentRecord:
    id: str
    storage_path: str
    document_type: str
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


class CleanConnection:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

    def execute(self, query, params=None, *, prepare=None):
        clean_params = self._clean_nuls(params)
        return self._conn.execute(query, clean_params, prepare=prepare)

    def _clean_nuls(self, params):
        if params is None:
            return None
        if isinstance(params, tuple):
            return tuple(self._clean_nuls(x) for x in params)
        if isinstance(params, list):
            return [self._clean_nuls(x) for x in params]
        if isinstance(params, dict):
            return {k: self._clean_nuls(v) for k, v in params.items()}
        if isinstance(params, str):
            return params.replace('\x00', '')
        return params

    def __getattr__(self, name):
        return getattr(self._conn, name)


class Repository:
    def connect(self):
        conn = psycopg.connect(settings.database_url, row_factory=dict_row)
        return CleanConnection(conn)

    def load_document(self, document_id: str) -> DocumentRecord:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, storage_path, document_type, title, content_source_type, page_count
                FROM documents
                WHERE id = %s
                """,
                (document_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Document not found: {document_id}")
        return DocumentRecord(
            id=str(row["id"]),
            storage_path=row["storage_path"],
            document_type=row["document_type"],
            title=row["title"] or "",
            content_source_type=row["content_source_type"] or "UNKNOWN",
            page_count=row["page_count"],
        )

    def load_chunks(self, document_id: str) -> list[TextChunk]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  id,
                  page_number,
                  page_start,
                  page_end,
                  section_title,
                  chunk_index,
                  chunk_type,
                  content,
                  token_count,
                  source_asset_id,
                  metadata_json
                FROM document_chunks
                WHERE document_id = %s
                ORDER BY chunk_index
                """,
                (document_id,),
            ).fetchall()
        return [
            TextChunk(
                id=str(row["id"]),
                page_number=row["page_number"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                section_title=row["section_title"],
                chunk_index=row["chunk_index"],
                chunk_type=row["chunk_type"] or "PARAGRAPH",
                content=row["content"] or "",
                token_count=row["token_count"],
                source_asset_id=str(row["source_asset_id"]) if row["source_asset_id"] else None,
                metadata_json=row["metadata_json"],
            )
            for row in rows
        ]

    def latest_generating_note_id(self, document_id: str) -> str:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM document_ai_notes
                WHERE document_id = %s AND status = 'GENERATING'
                ORDER BY note_version DESC, created_at DESC
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"No generating note found for document {document_id}")
        return str(row["id"])

    def ensure_notes_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_ai_notes (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  note_version INTEGER NOT NULL,
                  status VARCHAR(64) NOT NULL,
                  title VARCHAR(500),
                  markdown TEXT NOT NULL,
                  summary TEXT,
                  model_provider VARCHAR(64),
                  model_name VARCHAR(128),
                  prompt_version VARCHAR(64),
                  source_document_version VARCHAR(64),
                  quality_report_json TEXT,
                  metadata_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, note_version)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_ai_note_sections (
                  id UUID PRIMARY KEY,
                  note_id UUID NOT NULL,
                  document_id UUID NOT NULL,
                  section_index INTEGER NOT NULL,
                  section_type VARCHAR(64) NOT NULL,
                  heading VARCHAR(500),
                  markdown TEXT NOT NULL,
                  page_start INTEGER,
                  page_end INTEGER,
                  source_chunk_ids_json TEXT,
                  source_pages_json TEXT,
                  confidence DOUBLE PRECISION,
                  warnings_json TEXT,
                  metadata_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(note_id, section_index)
                )
                """
            )

    def save_ai_note(
        self,
        note_id: str,
        document_id: str,
        markdown: str,
        summary: str,
        provider: str,
        model: str,
        prompt_version: str,
        quality_report_json: str,
        metadata_json: str,
        sections: Iterable[AiNoteSection],
    ) -> None:
        sections = list(sections)
        self.ensure_notes_schema()
        with self.connect() as conn:
            conn.execute("DELETE FROM document_ai_note_sections WHERE note_id = %s", (note_id,))
            for section in sections:
                conn.execute(
                    """
                    INSERT INTO document_ai_note_sections (
                      id,
                      note_id,
                      document_id,
                      section_index,
                      section_type,
                      heading,
                      markdown,
                      page_start,
                      page_end,
                      source_chunk_ids_json,
                      source_pages_json,
                      confidence,
                      warnings_json,
                      metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        section.note_id,
                        section.document_id,
                        section.section_index,
                        section.section_type,
                        section.heading,
                        section.markdown,
                        section.page_start,
                        section.page_end,
                        section.source_chunk_ids_json,
                        section.source_pages_json,
                        section.confidence,
                        section.warnings_json,
                        section.metadata_json,
                    ),
                )
            conn.execute(
                """
                UPDATE document_ai_notes
                SET status = 'READY',
                    markdown = %s,
                    summary = %s,
                    model_provider = %s,
                    model_name = %s,
                    prompt_version = %s,
                    source_document_version = %s,
                    quality_report_json = %s,
                    metadata_json = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    markdown,
                    summary,
                    provider,
                    model,
                    prompt_version,
                    "chunks:v1",
                    quality_report_json,
                    metadata_json,
                    note_id,
                ),
            )

    def load_ai_note_sections(self, note_id: str) -> list[AiNoteSection]:
        self.ensure_notes_schema()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  id,
                  note_id,
                  document_id,
                  section_index,
                  section_type,
                  heading,
                  markdown,
                  page_start,
                  page_end,
                  source_chunk_ids_json,
                  source_pages_json,
                  confidence,
                  warnings_json,
                  metadata_json
                FROM document_ai_note_sections
                WHERE note_id = %s
                ORDER BY section_index
                """,
                (note_id,),
            ).fetchall()
        return [
            AiNoteSection(
                note_id=str(row["note_id"]),
                document_id=str(row["document_id"]),
                section_index=row["section_index"],
                section_type=row["section_type"],
                heading=row["heading"] or "",
                markdown=row["markdown"] or "",
                page_start=row["page_start"],
                page_end=row["page_end"],
                source_chunk_ids_json=row["source_chunk_ids_json"] or "[]",
                source_pages_json=row["source_pages_json"] or "[]",
                confidence=float(row["confidence"] or 0.0),
                warnings_json=row["warnings_json"] or "[]",
                metadata_json=row["metadata_json"],
                id=str(row["id"]),
            )
            for row in rows
        ]

    def save_ai_note_section(self, section: AiNoteSection) -> None:
        self.ensure_notes_schema()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO document_ai_note_sections (
                  id,
                  note_id,
                  document_id,
                  section_index,
                  section_type,
                  heading,
                  markdown,
                  page_start,
                  page_end,
                  source_chunk_ids_json,
                  source_pages_json,
                  confidence,
                  warnings_json,
                  metadata_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (note_id, section_index)
                DO UPDATE SET
                  section_type = EXCLUDED.section_type,
                  heading = EXCLUDED.heading,
                  markdown = EXCLUDED.markdown,
                  page_start = EXCLUDED.page_start,
                  page_end = EXCLUDED.page_end,
                  source_chunk_ids_json = EXCLUDED.source_chunk_ids_json,
                  source_pages_json = EXCLUDED.source_pages_json,
                  confidence = EXCLUDED.confidence,
                  warnings_json = EXCLUDED.warnings_json,
                  metadata_json = EXCLUDED.metadata_json
                """,
                (
                    str(uuid4()),
                    section.note_id,
                    section.document_id,
                    section.section_index,
                    section.section_type,
                    section.heading,
                    section.markdown,
                    section.page_start,
                    section.page_end,
                    section.source_chunk_ids_json,
                    section.source_pages_json,
                    section.confidence,
                    section.warnings_json,
                    section.metadata_json,
                ),
            )

    def update_ai_note_generation_progress(
        self,
        note_id: str,
        summary: str,
        provider: str,
        model: str,
        prompt_version: str,
        metadata_json: str,
        quality_report_json: str,
    ) -> None:
        self.ensure_notes_schema()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE document_ai_notes
                SET status = 'GENERATING',
                    summary = %s,
                    model_provider = %s,
                    model_name = %s,
                    prompt_version = %s,
                    quality_report_json = %s,
                    metadata_json = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    summary[:4000],
                    provider,
                    model,
                    prompt_version,
                    quality_report_json,
                    metadata_json,
                    note_id,
                ),
            )

    def fail_ai_note(self, note_id: str, error_message: str) -> None:
        self.ensure_notes_schema()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE document_ai_notes
                SET status = 'FAILED',
                    summary = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error_message[:4000], note_id),
            )

    def mark_processing(self, task_id: str, document_id: str, step: str, progress: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'PROCESSING',
                    current_step = %s,
                    progress = %s,
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (step, progress, task_id),
            )
            conn.execute(
                """
                UPDATE documents
                SET status = 'PROCESSING',
                    updated_at = NOW()
                WHERE id = %s
                """,
                (document_id,),
            )

    def save_parse_result(
        self,
        document_id: str,
        parser_name: str,
        page_count: int,
        extracted_text_length: int,
        extracted_text_preview: str,
        detected_content_source_type: str,
        source_confidence: float | None = None,
        source_distribution_json: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_parse_results (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL UNIQUE,
                  parser_name VARCHAR(100) NOT NULL,
                  page_count INTEGER NOT NULL,
                  extracted_text_length INTEGER NOT NULL,
                  extracted_text_preview TEXT,
                  detected_content_source_type VARCHAR(64) NOT NULL,
                  source_confidence DOUBLE PRECISION,
                  source_distribution_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute("ALTER TABLE document_parse_results ADD COLUMN IF NOT EXISTS source_confidence DOUBLE PRECISION")
            conn.execute("ALTER TABLE document_parse_results ADD COLUMN IF NOT EXISTS source_distribution_json TEXT")
            conn.execute(
                """
                INSERT INTO document_parse_results (
                  id,
                  document_id,
                  parser_name,
                  page_count,
                  extracted_text_length,
                  extracted_text_preview,
                  detected_content_source_type,
                  source_confidence,
                  source_distribution_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (document_id)
                DO UPDATE SET
                  parser_name = EXCLUDED.parser_name,
                  page_count = EXCLUDED.page_count,
                  extracted_text_length = EXCLUDED.extracted_text_length,
                  extracted_text_preview = EXCLUDED.extracted_text_preview,
                  detected_content_source_type = EXCLUDED.detected_content_source_type,
                  source_confidence = EXCLUDED.source_confidence,
                  source_distribution_json = EXCLUDED.source_distribution_json,
                  updated_at = NOW()
                """,
                (
                    str(uuid4()),
                    document_id,
                    parser_name,
                    page_count,
                    extracted_text_length,
                    extracted_text_preview,
                    detected_content_source_type,
                    source_confidence,
                    source_distribution_json,
                ),
            )
            conn.execute(
                """
                UPDATE documents
                SET page_count = %s,
                    content_source_type = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (page_count, detected_content_source_type, document_id),
            )

    def save_parse_manifest(self, document_id: str, manifest_json: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_parse_manifests (
                  document_id UUID PRIMARY KEY,
                  manifest_json TEXT NOT NULL,
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                INSERT INTO document_parse_manifests (document_id, manifest_json)
                VALUES (%s, %s)
                ON CONFLICT (document_id)
                DO UPDATE SET manifest_json = EXCLUDED.manifest_json, updated_at = NOW()
                """,
                (document_id, manifest_json),
            )

    def replace_chunks(self, document_id: str, chunks: Iterable[TextChunk]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_chunks (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  page_number INTEGER NOT NULL,
                  page_start INTEGER,
                  page_end INTEGER,
                  section_title VARCHAR(500),
                  chunk_index INTEGER NOT NULL,
                  chunk_type VARCHAR(64),
                  content TEXT NOT NULL,
                  token_count INTEGER,
                  source_asset_id UUID,
                  metadata_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, chunk_index)
                )
                """
            )
            conn.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_start INTEGER")
            conn.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_end INTEGER")
            conn.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_type VARCHAR(64)")
            conn.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS source_asset_id UUID")
            conn.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS metadata_json TEXT")
            conn.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
            for chunk in chunks:
                page_start = chunk.page_start or chunk.page_number
                page_end = chunk.page_end or page_start
                token_count = chunk.token_count if chunk.token_count is not None else len(chunk.content.split())
                conn.execute(
                    """
                    INSERT INTO document_chunks (
                      id,
                      document_id,
                      page_number,
                      page_start,
                      page_end,
                      section_title,
                      chunk_index,
                      chunk_type,
                      content,
                      token_count,
                      source_asset_id,
                      metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        document_id,
                        chunk.page_number,
                        page_start,
                        page_end,
                        chunk.section_title,
                        chunk.chunk_index,
                        chunk.chunk_type,
                        chunk.content,
                        token_count,
                        chunk.source_asset_id,
                        chunk.metadata_json,
                    ),
                )

    def replace_page_assets(self, document_id: str, assets: Iterable[PageAsset]) -> dict[int, str]:
        assets = list(assets)
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_page_assets (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  page_number INTEGER NOT NULL,
                  asset_type VARCHAR(64) NOT NULL,
                  image_path TEXT NOT NULL,
                  width INTEGER NOT NULL,
                  height INTEGER NOT NULL,
                  image_count INTEGER NOT NULL,
                  drawing_count INTEGER NOT NULL,
                  image_coverage DOUBLE PRECISION NOT NULL,
                  text_length INTEGER NOT NULL,
                  visual_summary TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, page_number, asset_type)
                )
                """
            )
            conn.execute("DELETE FROM document_page_assets WHERE document_id = %s", (document_id,))
            ids_by_page: dict[int, str] = {}
            for asset in assets:
                asset_id = str(uuid4())
                ids_by_page[asset.page_number] = asset_id
                conn.execute(
                    """
                    INSERT INTO document_page_assets (
                      id,
                      document_id,
                      page_number,
                      asset_type,
                      image_path,
                      width,
                      height,
                      image_count,
                      drawing_count,
                      image_coverage,
                      text_length,
                      visual_summary
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        asset_id,
                        asset.document_id,
                        asset.page_number,
                        asset.asset_type,
                        asset.image_path,
                        asset.width,
                        asset.height,
                        asset.image_count,
                        asset.drawing_count,
                        asset.image_coverage,
                        asset.text_length,
                        asset.visual_summary,
                    ),
                )
        return ids_by_page

    def replace_layout_blocks(self, document_id: str, blocks: Iterable[LayoutBlock]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_layout_blocks (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  page_number INTEGER NOT NULL,
                  block_index INTEGER NOT NULL,
                  block_type VARCHAR(64) NOT NULL,
                  content TEXT,
                  bbox_json TEXT,
                  section_title VARCHAR(500),
                  heading_path_json TEXT,
                  source_asset_id UUID,
                  confidence DOUBLE PRECISION,
                  metadata_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, page_number, block_index)
                )
                """
            )
            conn.execute("DELETE FROM document_layout_blocks WHERE document_id = %s", (document_id,))
            for block in blocks:
                conn.execute(
                    """
                    INSERT INTO document_layout_blocks (
                      id,
                      document_id,
                      page_number,
                      block_index,
                      block_type,
                      content,
                      bbox_json,
                      section_title,
                      heading_path_json,
                      source_asset_id,
                      confidence,
                      metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        block.document_id,
                        block.page_number,
                        block.block_index,
                        block.block_type,
                        block.content,
                        block.bbox_json,
                        block.section_title,
                        block.heading_path_json,
                        block.source_asset_id,
                        block.confidence,
                        block.metadata_json,
                    ),
                )

    def load_layout_blocks(self, document_id: str) -> list[LayoutBlock]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  document_id,
                  page_number,
                  block_index,
                  block_type,
                  content,
                  bbox_json,
                  section_title,
                  heading_path_json,
                  source_asset_id,
                  confidence,
                  metadata_json
                FROM document_layout_blocks
                WHERE document_id = %s
                ORDER BY page_number, block_index
                """,
                (document_id,),
            ).fetchall()
        return [
            LayoutBlock(
                document_id=str(row["document_id"]),
                page_number=row["page_number"],
                block_index=row["block_index"],
                block_type=row["block_type"],
                content=row["content"] or "",
                bbox_json=row["bbox_json"],
                section_title=row["section_title"],
                heading_path_json=row["heading_path_json"],
                source_asset_id=str(row["source_asset_id"]) if row["source_asset_id"] else None,
                confidence=row["confidence"],
                metadata_json=row["metadata_json"],
            )
            for row in rows
        ]

    def replace_visual_regions(self, document_id: str, regions: Iterable[VisualRegion]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_visual_regions (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  page_number INTEGER NOT NULL,
                  region_index INTEGER NOT NULL,
                  region_type VARCHAR(64) NOT NULL,
                  asset_path TEXT NOT NULL,
                  bbox_json TEXT,
                  page_asset_id UUID,
                  width INTEGER NOT NULL,
                  height INTEGER NOT NULL,
                  confidence DOUBLE PRECISION NOT NULL,
                  metadata_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, page_number, region_index)
                )
                """
            )
            conn.execute("DELETE FROM document_visual_regions WHERE document_id = %s", (document_id,))
            for region in regions:
                conn.execute(
                    """
                    INSERT INTO document_visual_regions (
                      id,
                      document_id,
                      page_number,
                      region_index,
                      region_type,
                      asset_path,
                      bbox_json,
                      page_asset_id,
                      width,
                      height,
                      confidence,
                      metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        region.document_id,
                        region.page_number,
                        region.region_index,
                        region.region_type,
                        region.asset_path,
                        region.bbox_json,
                        region.page_asset_id,
                        region.width,
                        region.height,
                        region.confidence,
                        region.metadata_json,
                    ),
                )

    def load_visual_regions(self, document_id: str) -> list[VisualRegion]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  document_id,
                  page_number,
                  region_index,
                  region_type,
                  asset_path,
                  bbox_json,
                  page_asset_id,
                  width,
                  height,
                  confidence,
                  metadata_json
                FROM document_visual_regions
                WHERE document_id = %s
                ORDER BY page_number, region_index
                """,
                (document_id,),
            ).fetchall()
        return [
            VisualRegion(
                document_id=str(row["document_id"]),
                page_number=row["page_number"],
                region_index=row["region_index"],
                region_type=row["region_type"],
                asset_path=row["asset_path"],
                bbox_json=row["bbox_json"],
                page_asset_id=str(row["page_asset_id"]) if row["page_asset_id"] else None,
                width=row["width"],
                height=row["height"],
                confidence=row["confidence"],
                metadata_json=row["metadata_json"],
            )
            for row in rows
        ]

    def replace_vlm_results(self, document_id: str, results: Iterable[VlmResult]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_vlm_results (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  page_number INTEGER NOT NULL,
                  region_index INTEGER NOT NULL,
                  region_type VARCHAR(64) NOT NULL,
                  provider VARCHAR(64) NOT NULL,
                  model VARCHAR(128) NOT NULL,
                  transcription TEXT,
                  description TEXT,
                  latex TEXT,
                  code TEXT,
                  uncertainty TEXT,
                  search_text TEXT,
                  raw_response_json TEXT,
                  error_message TEXT,
                  input_fingerprint VARCHAR(64),
                  attempt_count INTEGER NOT NULL DEFAULT 1,
                  content_kind VARCHAR(32),
                  importance VARCHAR(16),
                  reading_order TEXT,
                  language VARCHAR(32),
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, page_number, region_index, provider, model)
                )
                """
            )
            conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS input_fingerprint VARCHAR(64)")
            conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1")
            conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS content_kind VARCHAR(32)")
            conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS importance VARCHAR(16)")
            conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS reading_order TEXT")
            conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS language VARCHAR(32)")
            conn.execute("DELETE FROM document_vlm_results WHERE document_id = %s", (document_id,))
            for result in results:
                self._upsert_vlm_result_on_connection(conn, result)

    def ensure_vlm_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_vlm_results (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  page_number INTEGER NOT NULL,
                  region_index INTEGER NOT NULL,
                  region_type VARCHAR(64) NOT NULL,
                  provider VARCHAR(64) NOT NULL,
                  model VARCHAR(128) NOT NULL,
                  transcription TEXT,
                  description TEXT,
                  latex TEXT,
                  code TEXT,
                  uncertainty TEXT,
                  search_text TEXT,
                  raw_response_json TEXT,
                  error_message TEXT,
                  input_fingerprint VARCHAR(64),
                  attempt_count INTEGER NOT NULL DEFAULT 1,
                  content_kind VARCHAR(32),
                  importance VARCHAR(16),
                  reading_order TEXT,
                  language VARCHAR(32),
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, page_number, region_index, provider, model)
                )
                """
            )
            self._ensure_vlm_schema_on_connection(conn)

    def upsert_vlm_result(self, result: VlmResult) -> None:
        """Persist one region immediately so retries never regenerate successes."""
        with self.connect() as conn:
            self._ensure_vlm_schema_on_connection(conn)
            self._upsert_vlm_result_on_connection(conn, result)

    def _ensure_vlm_schema_on_connection(self, conn) -> None:
        conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS input_fingerprint VARCHAR(64)")
        conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1")
        conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS content_kind VARCHAR(32)")
        conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS importance VARCHAR(16)")
        conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS reading_order TEXT")
        conn.execute("ALTER TABLE document_vlm_results ADD COLUMN IF NOT EXISTS language VARCHAR(32)")

    def _upsert_vlm_result_on_connection(self, conn, result: VlmResult) -> None:
        conn.execute(
            "DELETE FROM document_vlm_results WHERE document_id = %s AND page_number = %s AND region_index = %s",
            (result.document_id, result.page_number, result.region_index),
        )
        conn.execute(
            """
            INSERT INTO document_vlm_results (
              id, document_id, page_number, region_index, region_type,
              provider, model, transcription, description, latex, code,
              uncertainty, search_text, raw_response_json, error_message,
              input_fingerprint, attempt_count
              , content_kind, importance, reading_order, language
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid4()), result.document_id, result.page_number, result.region_index,
                result.region_type, result.provider, result.model, result.transcription,
                result.description, result.latex, result.code, result.uncertainty,
                result.search_text, result.raw_response_json, result.error_message,
                result.input_fingerprint, result.attempt_count,
                result.content_kind, result.importance, result.reading_order, result.language,
            ),
        )

    def load_vlm_results(self, document_id: str) -> list[VlmResult]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                  document_id,
                  page_number,
                  region_index,
                  region_type,
                  provider,
                  model,
                  transcription,
                  description,
                  latex,
                  code,
                  uncertainty,
                  search_text,
                  raw_response_json,
                  error_message,
                  input_fingerprint,
                  attempt_count
                  , content_kind, importance, reading_order, language
                FROM document_vlm_results
                WHERE document_id = %s
                ORDER BY page_number, region_index
                """,
                (document_id,),
            ).fetchall()
        return [
            VlmResult(
                document_id=str(row["document_id"]),
                page_number=row["page_number"],
                region_index=row["region_index"],
                region_type=row["region_type"],
                provider=row["provider"],
                model=row["model"],
                transcription=row["transcription"] or "",
                description=row["description"] or "",
                latex=row["latex"] or "",
                code=row["code"] or "",
                uncertainty=row["uncertainty"] or "",
                search_text=row["search_text"] or "",
                raw_response_json=row["raw_response_json"],
                error_message=row["error_message"],
                input_fingerprint=row.get("input_fingerprint"),
                attempt_count=int(row.get("attempt_count") or 1),
                content_kind=row.get("content_kind") or "unknown",
                importance=row.get("importance") or "medium",
                reading_order=row.get("reading_order") or "",
                language=row.get("language") or "unknown",
            )
            for row in rows
        ]

    def replace_markdown_pages(self, document_id: str, pages: Iterable[MarkdownPage]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_markdown_pages (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  page_number INTEGER NOT NULL,
                  markdown TEXT NOT NULL,
                  source_type VARCHAR(64) NOT NULL,
                  quality_score DOUBLE PRECISION NOT NULL,
                  warnings_json TEXT,
                  structure_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  UNIQUE(document_id, page_number)
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_markdown_pages_document_page
                ON document_markdown_pages(document_id, page_number)
                """
            )
            conn.execute("DELETE FROM document_markdown_pages WHERE document_id = %s", (document_id,))
            for page in pages:
                conn.execute(
                    """
                    INSERT INTO document_markdown_pages (
                      id,
                      document_id,
                      page_number,
                      markdown,
                      source_type,
                      quality_score,
                      warnings_json,
                      structure_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid4()),
                        page.document_id,
                        page.page_number,
                        page.markdown,
                        page.source_type,
                        page.quality_score,
                        page.warnings_json,
                        page.structure_json,
                    ),
                )

    def save_markdown_document(self, document: MarkdownDocument) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_markdown_documents (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL UNIQUE,
                  markdown TEXT NOT NULL,
                  structure_json TEXT,
                  quality_report_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_markdown_documents_document
                ON document_markdown_documents(document_id)
                """
            )
            conn.execute(
                """
                INSERT INTO document_markdown_documents (
                  id,
                  document_id,
                  markdown,
                  structure_json,
                  quality_report_json
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (document_id)
                DO UPDATE SET
                  markdown = EXCLUDED.markdown,
                  structure_json = EXCLUDED.structure_json,
                  quality_report_json = EXCLUDED.quality_report_json,
                  updated_at = NOW()
                """,
                (
                    str(uuid4()),
                    document.document_id,
                    document.markdown,
                    document.structure_json,
                    document.quality_report_json,
                ),
            )

    def ensure_embedding_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS document_embeddings (
                  id UUID PRIMARY KEY,
                  document_id UUID NOT NULL,
                  source_domain VARCHAR(32),
                  source_object_type VARCHAR(64),
                  source_object_id UUID,
                  embedding_provider VARCHAR(64),
                  embedding_model VARCHAR(128),
                  embedding_dimension INTEGER,
                  content_hash VARCHAR(128),
                  embedding vector,
                  embedding_text TEXT,
                  text_preview TEXT,
                  metadata_json TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for statement in [
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS source_domain VARCHAR(32)",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS source_object_type VARCHAR(64)",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS source_object_id UUID",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS embedding_provider VARCHAR(64)",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(128)",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS content_hash VARCHAR(128)",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS text_preview TEXT",
                "ALTER TABLE document_embeddings ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            ]:
                conn.execute(statement)
            conn.execute(
                """
                DO $$
                BEGIN
                  IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_embeddings' AND column_name = 'source_table'
                  ) THEN
                    ALTER TABLE document_embeddings ALTER COLUMN source_table DROP NOT NULL;
                  END IF;
                  IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_embeddings' AND column_name = 'source_id'
                  ) THEN
                    ALTER TABLE document_embeddings ALTER COLUMN source_id DROP NOT NULL;
                  END IF;
                  IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_embeddings' AND column_name = 'content_kind'
                  ) THEN
                    ALTER TABLE document_embeddings ALTER COLUMN content_kind DROP NOT NULL;
                  END IF;
                  IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_embeddings' AND column_name = 'provider'
                  ) THEN
                    ALTER TABLE document_embeddings ALTER COLUMN provider DROP NOT NULL;
                  END IF;
                  IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'document_embeddings' AND column_name = 'model'
                  ) THEN
                    ALTER TABLE document_embeddings ALTER COLUMN model DROP NOT NULL;
                  END IF;
                END $$;
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_document_embeddings_source_provider_model
                ON document_embeddings(source_domain, source_object_type, source_object_id, embedding_provider, embedding_model)
                """
            )

    def load_embedding_sources(self, document_id: str, include_pdf: bool = True, include_ai_note: bool = True) -> list[EmbeddingSource]:
        sources: list[EmbeddingSource] = []
        if include_pdf:
            sources.extend(self.load_pdf_chunk_embedding_sources(document_id))
        if include_ai_note:
            sources.extend(self.load_ai_note_embedding_sources(document_id))
        return sources

    def load_pdf_chunk_embedding_sources(self, document_id: str) -> list[EmbeddingSource]:
        chunks = self.load_chunks(document_id)
        sources = []
        for chunk in chunks:
            page_start = chunk.page_start or chunk.page_number
            page_end = chunk.page_end or page_start
            title = chunk.section_title or f"Chunk {chunk.chunk_index}"
            embedding_text = "\n".join(
                [
                    "Source: PDF",
                    f"Pages: {page_start}-{page_end}",
                    f"Section: {title}",
                    f"Type: {chunk.chunk_type}",
                    "",
                    chunk.content,
                ]
            )
            metadata = {
                "pageStart": page_start,
                "pageEnd": page_end,
                "title": title,
                "chunkIndex": chunk.chunk_index,
                "chunkType": chunk.chunk_type,
                "tokenCount": chunk.token_count,
            }
            sources.append(
                EmbeddingSource(
                    document_id=document_id,
                    source_domain="PDF",
                    source_object_type="DOCUMENT_CHUNK",
                    source_object_id=chunk.id or "",
                    embedding_text=embedding_text,
                    text_preview=compact_preview(chunk.content),
                    metadata_json=json_dumps_compact(metadata),
                )
            )
        return [source for source in sources if source.source_object_id]

    def load_ai_note_embedding_sources(self, document_id: str) -> list[EmbeddingSource]:
        with self.connect() as conn:
            note = conn.execute(
                """
                SELECT id, note_version
                FROM document_ai_notes
                WHERE document_id = %s AND status = 'READY'
                ORDER BY note_version DESC
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        if note is None:
            return []
        sections = self.load_ai_note_sections(str(note["id"]))
        sources = []
        for section in sections:
            page_start = section.page_start
            page_end = section.page_end or page_start
            title = section.heading or f"AI Note Section {section.section_index}"
            embedding_text = "\n".join(
                [
                    "Source: AI Note",
                    f"Pages: {page_start}-{page_end}",
                    f"Heading: {title}",
                    f"Type: {section.section_type}",
                    "",
                    section.markdown,
                ]
            )
            metadata = {
                "pageStart": page_start,
                "pageEnd": page_end,
                "title": title,
                "noteId": str(note["id"]),
                "noteVersion": note["note_version"],
                "sectionIndex": section.section_index,
                "sectionType": section.section_type,
            }
            sources.append(
                EmbeddingSource(
                    document_id=document_id,
                    source_domain="AI_NOTE",
                    source_object_type="AI_NOTE_SECTION",
                    source_object_id=section.id or "",
                    embedding_text=embedding_text,
                    text_preview=compact_preview(section.markdown),
                    metadata_json=json_dumps_compact(metadata),
                )
            )
        return sources

    def existing_embedding_hashes(self, provider: str, model: str, sources: list[EmbeddingSource]) -> dict[tuple[str, str, str], str]:
        if not sources:
            return {}
        source_ids = [source.source_object_id for source in sources]
        placeholders = ",".join(["%s"] * len(source_ids))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT source_domain, source_object_type, source_object_id, content_hash
                FROM document_embeddings
                WHERE embedding_provider = %s
                  AND embedding_model = %s
                  AND source_object_id IN ({placeholders})
                """,
                (provider, model, *source_ids),
            ).fetchall()
        return {
            (row["source_domain"], row["source_object_type"], str(row["source_object_id"])): row["content_hash"]
            for row in rows
        }

    def upsert_embeddings(self, embeddings: Iterable[DocumentEmbedding]) -> None:
        rows = list(embeddings)
        if not rows:
            return
        self.ensure_embedding_schema()
        with self.connect() as conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO document_embeddings (
                      id,
                      document_id,
                      source_domain,
                      source_object_type,
                      source_object_id,
                      embedding_provider,
                      embedding_model,
                      embedding_dimension,
                      content_hash,
                      embedding_text,
                      text_preview,
                      embedding,
                      metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                    ON CONFLICT (source_domain, source_object_type, source_object_id, embedding_provider, embedding_model)
                    DO UPDATE SET
                      document_id = EXCLUDED.document_id,
                      embedding_dimension = EXCLUDED.embedding_dimension,
                      content_hash = EXCLUDED.content_hash,
                      embedding_text = EXCLUDED.embedding_text,
                      text_preview = EXCLUDED.text_preview,
                      embedding = EXCLUDED.embedding,
                      metadata_json = EXCLUDED.metadata_json,
                      updated_at = NOW()
                    """,
                    (
                        str(uuid4()),
                        row.document_id,
                        row.source_domain,
                        row.source_object_type,
                        row.source_object_id,
                        row.embedding_provider,
                        row.embedding_model,
                        row.embedding_dimension,
                        row.content_hash,
                        row.embedding_text,
                        row.text_preview,
                        vector_literal(row.embedding),
                        row.metadata_json,
                    ),
                )

    def mark_completed(self, task_id: str, document_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'COMPLETED',
                    current_step = 'COMPLETED',
                    progress = 100,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (task_id,),
            )
            conn.execute(
                """
                UPDATE documents
                SET status = 'READY',
                    updated_at = NOW()
                WHERE id = %s
                """,
                (document_id,),
            )

    def mark_failed(self, task_id: str, document_id: str, error_message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'FAILED',
                    current_step = 'FAILED',
                    progress = 100,
                    error_message = %s,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error_message[:4000], task_id),
            )
            conn.execute(
                """
                UPDATE documents
                SET status = 'FAILED',
                    updated_at = NOW()
                WHERE id = %s
                """,
                (document_id,),
            )

    def mark_task_processing(self, task_id: str, step: str, progress: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'PROCESSING',
                    current_step = %s,
                    progress = %s,
                    started_at = COALESCE(started_at, NOW()),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (step, progress, task_id),
            )

    def mark_task_completed(self, task_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'COMPLETED',
                    current_step = 'COMPLETED',
                    progress = 100,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (task_id,),
            )

    def mark_task_failed(self, task_id: str, error_message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'FAILED',
                    current_step = 'FAILED',
                    progress = 100,
                    error_message = %s,
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (error_message[:4000], task_id),
            )

    def recover_stale_generate_notes_tasks(self, stale_after_minutes: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH stale AS (
                  SELECT id
                  FROM tasks
                  WHERE task_type = 'GENERATE_NOTES'
                    AND status = 'PROCESSING'
                    AND updated_at < NOW() - (%s::text || ' minutes')::interval
                  ORDER BY updated_at
                  FOR UPDATE SKIP LOCKED
                )
                UPDATE tasks t
                SET status = 'RETRYING',
                    current_step = 'GENERATING_NOTES',
                    retry_count = retry_count + 1,
                    error_message = 'Recovered stale PROCESSING task and re-enqueued it.',
                    updated_at = NOW()
                FROM stale
                WHERE t.id = stale.id
                RETURNING t.id, t.document_id, t.user_id, t.task_type
                """,
                (stale_after_minutes,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_stale_parse_tasks(self, stale_after_minutes: int, max_retries: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH stale AS (
                  SELECT id
                  FROM tasks
                  WHERE task_type = 'PARSE_DOCUMENT'
                    AND status = 'PROCESSING'
                    AND updated_at < NOW() - (%s::text || ' minutes')::interval
                    AND retry_count < %s
                  ORDER BY updated_at
                  FOR UPDATE SKIP LOCKED
                )
                UPDATE tasks t
                SET status = 'RETRYING',
                    current_step = 'PARSING_PDF',
                    retry_count = retry_count + 1,
                    error_message = 'Recovered stale parse task; successful region checkpoints will be reused.',
                    updated_at = NOW()
                FROM stale
                WHERE t.id = stale.id
                RETURNING t.id, t.document_id, t.user_id, t.task_type
                """,
                (stale_after_minutes, max_retries),
            ).fetchall()
        return [dict(row) for row in rows]


def compact_preview(text: str, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def json_dumps_compact(value: dict) -> str:
    return json.dumps(value, separators=(",", ":"))


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(float(value), ".10g") for value in values) + "]"


# Canonical task-type and step lists shared by every schema owner that widens
# the Hibernate-generated CHECK constraints. A single source prevents two
# subsystems from fighting over the constraint definition.
ALL_TASK_TYPES = (
    "PARSE_DOCUMENT",
    "GENERATE_EMBEDDINGS",
    "GENERATE_NOTES",
    "GENERATE_FLASHCARDS",
    "GENERATE_QUIZ",
    "GRADE_QUIZ_ATTEMPT",
    "ANSWER_CONVERSATION_TURN",
    "MAINTAIN_CONVERSATION_MEMORY",
    "ASK_DOCUMENT",
    "EXPORT_MARKDOWN",
)

ALL_TASK_STEPS = (
    "UPLOADED",
    "PARSING_PDF",
    "EXTRACTING_TEXT",
    "ANALYZING_VISUAL_CONTENT",
    "CROPPING_VISUAL_REGIONS",
    "VLM_ANALYSIS",
    "LAYOUT_CHUNKING",
    "CHUNKING",
    "GENERATING_EMBEDDINGS",
    "GENERATING_NOTES",
    "GENERATING_FLASHCARDS",
    "GENERATING_QUIZ",
    "GRADING_QUIZ",
    "ANSWERING",
    "MAINTAINING_MEMORY",
    "COMPLETED",
    "FAILED",
)


def ensure_task_constraints(conn) -> None:
    """Widen the tasks CHECK constraints to the canonical type/step lists.

    Idempotent: rewrites a constraint only when the newest member is missing
    from its current definition.
    """
    sentinel_type = "MAINTAIN_CONVERSATION_MEMORY"
    type_check = conn.execute(
        """SELECT pg_get_constraintdef(oid) definition FROM pg_constraint
           WHERE conrelid='tasks'::regclass AND conname='tasks_task_type_check'"""
    ).fetchone()
    if not type_check or sentinel_type not in type_check["definition"]:
        conn.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_task_type_check")
        values = ",".join(f"'{item}'" for item in ALL_TASK_TYPES)
        conn.execute(f"ALTER TABLE tasks ADD CONSTRAINT tasks_task_type_check CHECK (task_type IN ({values}))")

    sentinel_step = "MAINTAINING_MEMORY"
    step_check = conn.execute(
        """SELECT pg_get_constraintdef(oid) definition FROM pg_constraint
           WHERE conrelid='tasks'::regclass AND conname='tasks_current_step_check'"""
    ).fetchone()
    if not step_check or sentinel_step not in step_check["definition"]:
        conn.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_current_step_check")
        values = ",".join(f"'{item}'" for item in ALL_TASK_STEPS)
        conn.execute(f"ALTER TABLE tasks ADD CONSTRAINT tasks_current_step_check CHECK (current_step IN ({values}))")
