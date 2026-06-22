# NoteFlow Database Schema

This document defines the first database schema for PDF upload, document management, async parsing tasks, and PDF parse results.

## 1. Scope

The first implementation focuses on:

1. User-owned documents.
2. PDF upload metadata.
3. Async processing tasks.
4. PDF parse status and summary.
5. Extracted layout blocks and chunks.
6. Visual regions and VLM analysis results.
7. Future-ready fields for embeddings, semantic search, notes, quiz, and citations.

## 2. Enums

### document_type

`document_type` describes the academic purpose of the uploaded file.

```text
COURSE_NOTES
LECTURE_SLIDES
RESEARCH_PAPER
TEXTBOOK_CHAPTER
ASSIGNMENT
PAST_EXAM
HANDWRITTEN_NOTES
OTHER
```

### content_source_type

`content_source_type` describes how the content is physically represented inside the PDF.

```text
TEXT_PDF
SCANNED_PDF
HANDWRITTEN_SCAN
MIXED
UNKNOWN
```

For MVP, users choose `document_type`, while the parser may infer `content_source_type` from extracted text volume.

### document_status

```text
UPLOADED
PROCESSING
READY
FAILED
DELETED
```

### task_type

```text
PARSE_DOCUMENT
GENERATE_EMBEDDINGS
GENERATE_NOTES
GENERATE_QUIZ
ASK_DOCUMENT
EXPORT_MARKDOWN
```

### task_status

```text
PENDING
PROCESSING
COMPLETED
FAILED
RETRYING
CANCELLED
```

### task_step

```text
UPLOADED
PARSING_PDF
EXTRACTING_TEXT
ANALYZING_VISUAL_CONTENT
CROPPING_VISUAL_REGIONS
VLM_ANALYSIS
LAYOUT_CHUNKING
CHUNKING
GENERATING_EMBEDDINGS
GENERATING_NOTES
COMPLETED
FAILED
```

## 3. Tables

### users

For MVP, this can be stubbed or mapped to a third-party auth provider later.

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY,
  email VARCHAR(255) NOT NULL UNIQUE,
  display_name VARCHAR(255),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### documents

Stores uploaded PDF metadata and high-level processing state.

```sql
CREATE TABLE documents (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL,
  title VARCHAR(500) NOT NULL,
  original_filename VARCHAR(500) NOT NULL,
  file_type VARCHAR(100) NOT NULL,
  file_size BIGINT NOT NULL,
  storage_path TEXT NOT NULL,
  page_count INTEGER,
  language VARCHAR(32),
  document_type VARCHAR(64) NOT NULL,
  content_source_type VARCHAR(64) NOT NULL DEFAULT 'UNKNOWN',
  status VARCHAR(64) NOT NULL DEFAULT 'UPLOADED',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_documents_user FOREIGN KEY (user_id) REFERENCES users(id)
);
```

Recommended indexes:

```sql
CREATE INDEX idx_documents_user_created_at ON documents(user_id, created_at DESC);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_type ON documents(document_type);
```

### tasks

Stores async work status for parsing and later AI operations.

```sql
CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL,
  user_id UUID NOT NULL,
  task_type VARCHAR(64) NOT NULL,
  status VARCHAR(64) NOT NULL DEFAULT 'PENDING',
  current_step VARCHAR(64) NOT NULL DEFAULT 'UPLOADED',
  progress INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_tasks_document FOREIGN KEY (document_id) REFERENCES documents(id),
  CONSTRAINT fk_tasks_user FOREIGN KEY (user_id) REFERENCES users(id)
);
```

Recommended indexes:

```sql
CREATE INDEX idx_tasks_document_created_at ON tasks(document_id, created_at DESC);
CREATE INDEX idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX idx_tasks_status_created_at ON tasks(status, created_at);
```

### document_parse_results

Stores parser-level summary information. The full text can stay here for early MVP, but later it is better to rely on chunks.

```sql
CREATE TABLE document_parse_results (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL UNIQUE,
  parser_name VARCHAR(100) NOT NULL,
  page_count INTEGER NOT NULL,
  extracted_text_length INTEGER NOT NULL,
  extracted_text_preview TEXT,
  detected_content_source_type VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_parse_results_document FOREIGN KEY (document_id) REFERENCES documents(id)
);
```

### document_chunks

Stores retrieval chunks generated from layout blocks and visual analysis.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE document_chunks (
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
  CONSTRAINT fk_chunks_document FOREIGN KEY (document_id) REFERENCES documents(id),
  CONSTRAINT uq_chunks_document_index UNIQUE(document_id, chunk_index)
);
```

Recommended indexes:

```sql
CREATE INDEX idx_chunks_document_page ON document_chunks(document_id, page_number);
CREATE INDEX idx_chunks_document_index ON document_chunks(document_id, chunk_index);
```

### document_markdown_pages

Stores the page-level Markdown intermediate representation. This layer is generated before retrieval chunks so that text, formulas, code, tables, diagrams, and handwritten regions can be inspected and cleaned before embedding.

```sql
CREATE TABLE document_markdown_pages (
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
);
```

### document_markdown_documents

Stores the stitched document-level Markdown used for later structural chunking.

```sql
CREATE TABLE document_markdown_documents (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL UNIQUE,
  markdown TEXT NOT NULL,
  structure_json TEXT,
  quality_report_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### document_layout_blocks

Stores normalized coordinate-aware blocks before final chunking.

```sql
CREATE TABLE document_layout_blocks (
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
);
```

### document_page_assets

Stores rendered full-page PNG assets.

```sql
CREATE TABLE document_page_assets (
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
);
```

### document_visual_regions

Stores cropped visual regions such as code screenshots, figures, diagrams, and handwritten regions.

```sql
CREATE TABLE document_visual_regions (
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
);
```

### document_vlm_results

Stores Vision Language Model analysis for each visual region.

```sql
CREATE TABLE document_vlm_results (
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
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(document_id, page_number, region_index, provider, model)
);
```

### document_embeddings

Reserved pgvector table for later text and image embedding generation.

```sql
CREATE TABLE document_embeddings (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL,
  source_table VARCHAR(64) NOT NULL,
  source_id UUID NOT NULL,
  content_kind VARCHAR(64) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  model VARCHAR(128) NOT NULL,
  embedding vector,
  embedding_text TEXT,
  metadata_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(document_id, source_table, source_id, provider, model)
);
```

When embeddings are implemented, add an index appropriate for the chosen vector dimension/model:

```sql
CREATE INDEX idx_document_embeddings_vector
ON document_embeddings
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

## 4. MVP Status Flow

Upload flow:

```text
documents.status = UPLOADED
tasks.status = PENDING
tasks.current_step = UPLOADED
```

Worker starts parsing:

```text
documents.status = PROCESSING
tasks.status = PROCESSING
tasks.current_step = PARSING_PDF
```

Worker extracts text:

```text
tasks.current_step = EXTRACTING_TEXT
tasks.progress = 40
```

Worker chunks text:

```text
tasks.current_step = CHUNKING
tasks.progress = 70
```

Worker finishes:

```text
documents.status = READY
tasks.status = COMPLETED
tasks.current_step = COMPLETED
tasks.progress = 100
```

Worker fails:

```text
documents.status = FAILED
tasks.status = FAILED
tasks.current_step = FAILED
tasks.error_message = <reason>
```

## 5. First APIs Backed By This Schema

```text
POST /documents
GET /documents
GET /documents/{id}
GET /tasks/{id}
GET /documents/{id}/tasks
```

The upload API creates both a `documents` row and a `tasks` row, then enqueues the task for the Python worker.
