# NoteFlow Pipeline Technical Specification

This document is the source of truth for NoteFlow's current document pipeline:

```text
PDF upload
  -> PDF parsing
  -> PDF-to-Markdown
  -> Markdown-to-chunks
  -> AI notes generation
  -> exported Markdown notes
```

Current downstream progress:

```text
Embedding generation: implemented
Hybrid vector/lexical/exact retrieval: implemented
RRF, reranking, HyDE, citations, and context construction: implemented
Multi-turn conversational answer generation: first vertical implemented
(persistent messages, source scope, memory context, vector evidence, structured
LLM answer, durable citations, Java REST, browser polling). Streaming and the
broader LangGraph/tool workflow remain planned in
MULTI_TURN_CONVERSATIONAL_RAG_ARCHITECTURE.md.
```

It consolidates the older workflow, architecture, document type, PDF Markdown, chunking, and AI notes documents into one layered technical reference. Historical audit reports remain useful, but this document describes the current intended implementation.

## 1. System Layers

```text
Browser frontend
  -> Spring Boot API
  -> PostgreSQL metadata/content tables
  -> Redis document task queue
  -> Python worker
  -> local runtime storage
  -> optional Gemini/OpenAI VLM and notes providers
```

### 1.1 Frontend

The web app uploads PDFs, polls task status, displays parse output, chunks, visual regions, Markdown, and AI notes.

### 1.2 Spring Boot API

The API owns:

1. User-facing HTTP endpoints.
2. Upload validation and file persistence.
3. `documents` and `tasks` records.
4. Task dispatch into Redis after database transaction commit.
5. Read APIs for parse results, Markdown, chunks, visual regions, and AI notes.

Important classes:

```text
services/api/src/main/java/com/noteflow/documents/DocumentService.java
services/api/src/main/java/com/noteflow/tasks/TaskDispatchService.java
services/api/src/main/java/com/noteflow/notes/DocumentAiNoteService.java
```

### 1.3 Python Worker

The worker owns expensive asynchronous work:

1. PDF parsing and rendering.
2. Visual region extraction.
3. VLM analysis.
4. Markdown construction.
5. Chunking.
6. AI notes generation.
7. Stale notes task recovery.

Important entrypoints:

```text
services/worker/noteflow_worker/main.py
services/worker/noteflow_worker/pipelines/parse_document.py
services/worker/noteflow_worker/pipelines/generate_notes.py
```

The worker runs up to:

```text
worker_max_concurrent_tasks = 3
```

Each AI notes task can send up to:

```text
notes_max_concurrent_requests = 3
```

This gives a maximum of 9 concurrent AI note generation requests when three note tasks are active.

## 2. Runtime Storage Layout

Local runtime files are under:

```text
services/api/storage/
```

| Path | Owner | Purpose |
|---|---|---|
| `services/api/storage/uploads/{document_id}.pdf` | API | Original uploaded PDF. |
| `services/api/storage/rendered/{document_id}/page-XXX.png` | Worker | Full rendered page images. |
| `services/api/storage/regions/{document_id}/page-XXX-region-YY.png` | Worker | Cropped visual regions from pages. |
| `exported_ai_notes/*.md` | Manual/export script | Exported READY AI notes for inspection or sharing. |

PostgreSQL is the canonical metadata store. File paths are stored in database tables and should not be moved manually without a database migration.

## 3. Primary HTTP Interfaces

### 3.1 Upload Document

```http
POST /documents
Content-Type: multipart/form-data
```

Request parts:

| Field | Type | Required | Description |
|---|---|---:|---|
| `file` | PDF file | Yes | Original uploaded PDF. |
| `documentType` | enum | No | User intent/type. Defaults to `OTHER`. |
| `title` | string | No | Display title. Defaults to original filename. |

Response:

```json
{
  "documentId": "uuid",
  "taskId": "uuid",
  "status": "UPLOADED"
}
```

Side effects:

1. Stores PDF at `services/api/storage/uploads/{document_id}.pdf`.
2. Inserts `documents`.
3. Inserts `tasks` with `task_type = PARSE_DOCUMENT`.
4. Enqueues Redis payload after transaction commit.

### 3.2 Task Status

```http
GET /tasks/{taskId}
GET /documents/{documentId}/tasks
GET /tasks
```

Important task statuses:

```text
PENDING
PROCESSING
RETRYING
COMPLETED
FAILED
CANCELLED
```

Important parse steps:

```text
UPLOADED
PARSING_PDF
EXTRACTING_TEXT
ANALYZING_VISUAL_CONTENT
CROPPING_VISUAL_REGIONS
VLM_ANALYSIS
LAYOUT_CHUNKING
CHUNKING
COMPLETED
FAILED
```

### 3.3 Parsed Document Inspection

```http
GET /documents/{documentId}/parse-result
GET /documents/{documentId}/assets
GET /assets/{assetId}
GET /documents/{documentId}/visual-regions
GET /visual-regions/{regionId}/asset
GET /documents/{documentId}/vlm-results
GET /documents/{documentId}/layout-blocks
GET /documents/{documentId}/markdown-pages
GET /documents/{documentId}/markdown
GET /documents/{documentId}/chunks
```

These endpoints are read-only inspection APIs for parser quality, visual debugging, source grounding, and retrieval development.

### 3.4 AI Notes

```http
POST /documents/{documentId}/notes
GET /documents/{documentId}/notes
GET /notes/{noteId}/sections
```

`POST /documents/{documentId}/notes`:

1. Requires the document status to be `READY`.
2. Reuses an existing active `GENERATING` note and active task when present.
3. If a `GENERATING` note exists but no active task exists, creates a new `GENERATE_NOTES` task to resume from saved sections.
4. Creates a new note version only when no active/generating note exists.

## 4. Redis Task Payload

Redis queue:

```text
queue:document-analysis
```

Payload:

```json
{
  "taskId": "uuid",
  "documentId": "uuid",
  "userId": "uuid",
  "taskType": "PARSE_DOCUMENT | GENERATE_NOTES"
}
```

The Java API enqueues after database commit. The Python worker consumes with `BLPOP`.

Because Redis `BLPOP` removes the item from the queue, the worker also supports stale note task recovery:

```text
notes_stale_task_after_minutes = 10
```

Stale `PROCESSING` `GENERATE_NOTES` tasks are moved to `RETRYING` and re-enqueued by:

```text
services/worker/scripts/recover_stale_notes_tasks.py
```

The worker also runs stale recovery at startup.

## 5. Database Ownership By Stage

| Stage | Tables | Written By | Read By |
|---|---|---|---|
| Upload | `documents`, `tasks` | API | API, worker |
| Parse summary | `document_parse_results` | Worker | API/frontend |
| Page assets | `document_page_assets` | Worker | API/frontend |
| Visual regions | `document_visual_regions` | Worker | API/frontend, VLM stage |
| VLM outputs | `document_vlm_results` | Worker | Markdown builder, API/frontend |
| Layout blocks | `document_layout_blocks` | Worker | Markdown/chunk builder, API/frontend |
| Markdown pages | `document_markdown_pages` | Worker | API/frontend, quality inspection |
| Markdown document | `document_markdown_documents` | Worker | API/frontend, notes generator |
| Chunks | `document_chunks` | Worker | API/frontend, future embeddings/RAG, notes generator |
| AI notes | `document_ai_notes` | Worker/API task creation | API/frontend/export |
| AI note sections | `document_ai_note_sections` | Worker | API/frontend/export/rebuild |
| Future embeddings | `document_embeddings` | Worker | Search/RAG |

## 6. Type System

### 6.1 `document_type`

`document_type` is user intent and academic structure.

Supported values:

```text
COURSE_NOTES
LECTURE_SLIDES
RESEARCH_PAPER
ASSIGNMENT
PAST_EXAM
HANDWRITTEN_NOTES
OTHER
```

`TEXTBOOK_CHAPTER` is intentionally not a supported product type for now. It overlaps with course notes, research papers, and generic documents.

### 6.2 `content_source_type`

`content_source_type` is detected physical/source form.

Current values:

```text
TEXT_PDF
MIXED
SCANNED_PDF
HANDWRITTEN_SCAN
UNKNOWN
```

`document_type` decides expected academic structure. `content_source_type` can override processing when the physical PDF is scanned or handwritten.

## 7. Processing Strategy Resolver

Implemented in:

```text
services/worker/noteflow_worker/pdf/strategies.py
```

Resolution priority:

1. If `document_type == HANDWRITTEN_NOTES`, force full-page VLM.
2. Else if `content_source_type in {SCANNED_PDF, HANDWRITTEN_SCAN}`, force page-level visual/VLM Markdown.
3. Else route by `document_type`.
4. Else use conservative mixed fallback.

Returned fields:

```text
document_type
content_source_type
markdown_strategy
chunk_strategy
force_full_page_vlm
require_vlm_success
```

Strategy matrix:

| Condition | Markdown Strategy | Chunk Strategy | VLM Success Required |
|---|---|---|---:|
| `HANDWRITTEN_NOTES` | `FULL_PAGE_VLM` | `PAGE_AWARE` | Yes |
| `SCANNED_PDF` / `HANDWRITTEN_SCAN` | `PAGE_LEVEL_VISUAL` | `PAGE_AWARE` | Yes |
| `LECTURE_SLIDES` | `SLIDE_LAYOUT` | `SLIDE_AWARE` | No |
| `COURSE_NOTES` | `STRUCTURAL_NOTES` | `TOPIC_AWARE` | No |
| `RESEARCH_PAPER` | `PAPER_SECTIONS` | `PAPER_SECTION_AWARE` | No |
| `ASSIGNMENT` / `PAST_EXAM` | `QUESTION_STRUCTURE` | `QUESTION_AWARE` | No |
| `OTHER` | `MIXED_LAYOUT` | `MIXED_FALLBACK` | No |

## 8. PDF To Markdown Workflow

Worker entrypoint:

```text
services/worker/noteflow_worker/pipelines/parse_document.py
```

Common steps:

1. Mark task/document as processing.
2. Load `documents`.
3. Validate `storage_path`.
4. Parse basic PDF text and page count.
5. Detect `content_source_type`.
6. Resolve processing strategy.
7. Render pages to images.
8. Write `document_page_assets`.
9. Build visual regions if needed.
10. Run VLM where selected/required.
11. Build layout blocks.
12. Build page Markdown and full Markdown document.
13. Build chunks.
14. Write parse result and mark task/document complete.

### 8.1 Full-Page VLM Route

Used for:

```text
document_type == HANDWRITTEN_NOTES
content_source_type in {SCANNED_PDF, HANDWRITTEN_SCAN}
```

Input:

```text
Original PDF
Rendered page images
document_type
content_source_type
```

Processing:

1. Render every page into `storage/rendered/{document_id}/page-XXX.png`.
2. Create one `document_visual_regions` row per page.
3. Region type is `HANDWRITTEN` or `FULL_PAGE_VISUAL`.
4. Send every page image to the configured VLM provider.
5. Store `document_vlm_results`.
6. Convert each VLM result into a page layout block.
7. Build one Markdown page per PDF page.
8. Build page-aware chunks.

Output:

```text
document_page_assets
document_visual_regions
document_vlm_results
document_layout_blocks
document_markdown_pages
document_markdown_documents
document_chunks
```

Failure behavior:

1. VLM retries.
2. If still failing, the parse task fails.
3. The worker must not silently write an empty page and continue.

### 8.2 Native Text / Mixed Route

Used for:

```text
TEXT_PDF
MIXED
UNKNOWN with extractable text
```

Input:

```text
Original PDF
Rendered page images
Native text/layout from PyMuPDF
Selected VLM visual region outputs
```

Processing:

1. Render every page.
2. Analyze visual density, image count, drawings, and text length.
3. Crop visual regions from PDF image blocks when available.
4. Filter likely decorative or repeated low-value images.
5. Preserve high-information regions such as code images, handwritten regions, and full-page fallback regions.
6. Run VLM on selected regions.
7. Extract coordinate-aware layout text.
8. Merge native text blocks and VLM-enriched visual blocks.
9. Build per-page Markdown.
10. Build full-document Markdown.
11. Build strategy-aware chunks.

Failure behavior:

1. Selective VLM region failures are stored in `document_vlm_results.error_message`.
2. Native text can continue when visual fallback fails.
3. Empty visually meaningful pages should be flagged, not silently treated as complete.

## 9. Multi-Modal Content Policy

The PDF-to-Markdown layer must classify and preserve source meaning before chunking. Chunking and AI notes should not be expected to recover information missing from Markdown.

| Source Content | Detection Signal | Markdown Treatment | Chunk Treatment |
|---|---|---|---|
| Native paragraph text | PDF text blocks | Preserve reading order as paragraphs. | Merge by heading/topic and token budget. |
| Headings | Font/layout heuristics, Markdown headings | Use Markdown headings. | Boundary candidates. |
| Math formulas | PDF glyphs, formula-like lines, VLM LaTeX | Normalize into readable LaTeX-ish Markdown. Do not delete private-use glyphs blindly. | Keep with adjacent explanation. Standalone only if large. |
| Code text | Monospace/layout/code heuristics | Preserve fenced code blocks. | Keep code with explanation; `CODE` chunk when dominant. |
| Code screenshots | Visual region classified `CODE_IMAGE` | VLM transcribes/explains code; convert to code block where possible. | Keep with page/topic; standalone if large. |
| Tables | Layout/table-like blocks | Use Markdown table when small and reliable; otherwise labeled bullets. | Keep table with caption/explanation; standalone if large. |
| Meaningful diagram/chart | `DIAGRAM`, dense visual region, chart-like image | VLM description plus extracted labels/formulas. | `MIXED_VISUAL` or diagram explanation chunk, linked to page asset. |
| Decorative image | repeated logo, tiny image, low semantic value | Filter or mark boilerplate/decorative. | Exclude from chunks unless needed for context. |
| Handwritten notes | `HANDWRITTEN_NOTES`, `HANDWRITTEN_SCAN`, handwritten visual region | Full-page VLM transcription; preserve derivation order and uncertainty. | Page-aware chunks, usually no semantic overlap. |
| Blank/low-content page | low text and low visual content | Preserve minimal marker only if useful. | Usually skipped. |

### 9.1 Meaningful vs Decorative Images

Images are meaningful when they contain:

1. Code.
2. Equations or derivations.
3. Graphs, plots, diagrams, charts.
4. Tables or handwritten notes.
5. Dense explanatory slide content not present in native text.

Images are likely decorative when they are:

1. Repeated logos.
2. Very small icons.
3. Background decoration.
4. Repeated low-information assets.

Repeated-image filtering must not remove `CODE_IMAGE`, `HANDWRITTEN`, or `FULL_PAGE_VISUAL` regions solely because their perceptual hashes are similar.

## 10. Markdown To Chunk Workflow

Implemented in:

```text
services/worker/noteflow_worker/pdf/layout.py
```

Input:

```text
document_markdown_documents.markdown
document_layout_blocks
document_vlm_results
document_page_assets
document_type
content_source_type
chunk_strategy
```

Output:

```text
document_chunks
```

Each chunk stores:

```text
document_id
page_number
page_start
page_end
section_title
chunk_index
chunk_type
content
token_count
source_asset_id
metadata_json
```

Chunk metadata includes:

```json
{
  "documentType": "COURSE_NOTES",
  "contentSourceType": "TEXT_PDF",
  "chunkStrategy": "TOPIC_AWARE",
  "headings": ["Chapter 3", "Example"],
  "blockTypes": ["HEADING", "FORMULA", "PARAGRAPH"],
  "containsImage": false,
  "containsTable": false,
  "containsFormula": true,
  "containsCode": false,
  "assetIds": [],
  "bboxRefs": []
}
```

### 10.1 Chunk Strategies

| Strategy | Used For | Boundary Rules |
|---|---|---|
| `PAGE_AWARE` | Scanned PDFs and handwritten notes | Keep page order. Merge short same-topic pages. Split only oversized pages. No semantic overlap. |
| `SLIDE_AWARE` | Lecture slides | Preserve slide boundaries. Merge short adjacent slides with same topic. Keep visuals with slide. |
| `TOPIC_AWARE` | Course notes | Prefer definitions, theorems, examples, proofs, solutions, remarks. Allow cross-page continuation. |
| `PAPER_SECTION_AWARE` | Research papers | Abstract, introduction, methods, results, discussion, references. |
| `QUESTION_AWARE` | Assignments and exams | Prefer one question/subpart with constraints, code, marks, diagrams. |
| `MIXED_FALLBACK` | Other/unknown | Conservative section/page chunks. |

Token budgets are strategy-specific and defined in `layout.py`. Current note-generation grouping uses larger groups than retrieval chunks:

```text
notes_group_target_tokens = 3200
notes_group_max_tokens = 4500
```

## 11. AI Notes Workflow

Worker entrypoint:

```text
services/worker/noteflow_worker/pipelines/generate_notes.py
```

Input:

```text
documents
document_chunks
document_markdown_documents
document_vlm_results and metadata indirectly through chunks
```

Output:

```text
document_ai_notes
document_ai_note_sections
```

Processing:

1. Load the latest `GENERATING` note for the document.
2. Load chunks.
3. Build source groups.
4. Load existing saved sections.
5. Detect completed source groups from section metadata.
6. Submit pending groups to the notes provider with up to `notes_max_concurrent_requests` parallel requests.
7. Save each group section immediately into `document_ai_note_sections`.
8. If one group fails after retries, keep the note `GENERATING`, mark task failed, and report resumable progress.
9. On retry, skip completed groups and regenerate only missing/failed groups.
10. When all groups complete, sort sections by `sourceGroupIndex` and `sourceGroupSectionIndex`.
11. Assemble one final Markdown document in `document_ai_notes.markdown`.
12. Mark note and task complete.

The final user-facing note is one Markdown string. Section rows are internal structured records for source traceability, resume, rebuild, editor features, and future partial regeneration.

### 11.1 AI Notes Provider

Configured by:

```text
NOTES_PROVIDER=gemini | openai | disabled
GEMINI_API_KEY=...
OPENAI_API_KEY=...
GEMINI_NOTES_MODEL=gemini-2.5-flash
OPENAI_NOTES_MODEL=gpt-4o-mini
```

The notes generator is AI-first. It must not fabricate a non-AI substitute when the provider is disabled.

### 11.2 Resume And Offline Rebuild

Resume is driven by saved section metadata:

```json
{
  "sourceGroupIndex": 4,
  "sourceGroupSectionIndex": 2,
  "sourceGroupSectionCount": 18,
  "sourceChunkIndexes": [35, 36, 37]
}
```

If final Markdown ordering or formatting needs to be repaired without re-calling AI, use:

```text
services/worker/scripts/rebuild_ai_note_markdown.py
```

This script:

1. Reads existing `document_ai_note_sections`.
2. Sorts by source group and section index.
3. Reassembles `document_ai_notes.markdown`.
4. Updates summaries.
5. Does not create notes tasks.
6. Does not call Gemini/OpenAI.

## 12. Exported AI Notes

READY notes can be exported to:

```text
exported_ai_notes/
```

Exported files follow:

```text
{Document_Title}_-_AI_Notes_v{note_version}_{status}.md
```

Current export behavior should include only `READY` notes with non-empty Markdown. Test documents such as `Smoke Test PDF` should be excluded from student-facing exports even if exported for debugging.

## 13. Quality Gates

### 13.1 Parse Quality

Before considering a document parse usable:

1. Page count must match the PDF.
2. `document_markdown_pages` should have one row per page.
3. Visually meaningful pages must not become silent empty Markdown.
4. Required VLM routes must have no failed required page results.
5. `document_chunks` must cover the same logical page range as Markdown.

### 13.2 AI Notes Quality

Before considering AI notes student-usable:

1. `document_ai_notes.status = READY`.
2. All source groups are completed.
3. Final Markdown covers the full document page range.
4. Source groups are in nondecreasing page order.
5. No `timed out`, `paused`, or provider error strings appear in final Markdown.
6. Source citations exist for major sections.
7. Large course notes must not have failed group content appended out of order.

### 13.3 Known Current Limitations

1. Some short documents have coarse source citations, such as `pages 1-17`.
2. Native text extraction can still produce imperfect math; the math normalizer improves but does not fully solve all formulas.
3. Selective visual regions for native text PDFs can fail without failing the parse.
4. Embeddings and semantic search are planned but not yet the primary completed workflow. See `docs/technical/EMBEDDING_SEARCH_RAG_PLAN.md`.
5. Exported debug/test notes should be filtered before student-facing release.

## 14. Configuration Summary

API:

```text
SPRING_DATASOURCE_URL
SPRING_DATASOURCE_USERNAME
SPRING_DATASOURCE_PASSWORD
REDIS_HOST
REDIS_PORT
NOTEFLOW_UPLOAD_DIR
NOTEFLOW_DOCUMENT_QUEUE
NOTEFLOW_DEV_USER_ID
```

Worker:

```text
DATABASE_URL
REDIS_URL
DOCUMENT_QUEUE
WORKER_MAX_CONCURRENT_TASKS
VISION_PROVIDER
GEMINI_API_KEY
OPENAI_API_KEY
VISION_REQUEST_TIMEOUT_SECONDS
VISION_REQUEST_MAX_ATTEMPTS
NOTES_PROVIDER
GEMINI_NOTES_MODEL
OPENAI_NOTES_MODEL
NOTES_REQUEST_TIMEOUT_SECONDS
NOTES_REQUEST_MAX_ATTEMPTS
NOTES_MAX_CONCURRENT_REQUESTS
NOTES_STALE_TASK_AFTER_MINUTES
NOTES_GROUP_TARGET_TOKENS
NOTES_GROUP_MAX_TOKENS
```

The Python settings currently use snake_case names through Pydantic settings. Environment variable names should match Pydantic's default uppercase conversion.

## 15. Current Recommended Development Flow

1. Upload a PDF through the web app.
2. Poll task status until parse completes.
3. Inspect parse result, Markdown pages, chunks, visual regions, and VLM results.
4. Fix PDF-to-Markdown quality before judging chunk quality.
5. Fix chunk strategy before generating AI notes.
6. Generate AI notes only after chunks look acceptable.
7. If AI notes pause because one group timed out, click Generate Notes again to resume.
8. If final Markdown order/formatting needs a mechanical fix, run offline rebuild rather than re-calling the AI provider.
9. Export READY notes for review.
10. Run quality checks before using exported notes as student-facing material.
