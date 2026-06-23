# PDF Upload, Markdown, And Chunk Pipeline

This document describes the current NoteFlow flow from uploaded PDF to Markdown and chunks.

For the full end-to-end contract, including HTTP interfaces, task dispatch, AI notes, exports, and quality gates, see:

```text
docs/technical/NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md
```

Scope:

1. How uploaded PDFs are stored.
2. How PDFs are parsed into Markdown.
3. How different document/content types are handled.
4. How Markdown is parsed into chunks.
5. Where outputs are stored on disk and in PostgreSQL.

## 1. End-To-End Flow

```text
User uploads PDF
  -> API stores original PDF
  -> API creates document + task records
  -> Worker parses PDF
  -> Worker renders page images
  -> Worker extracts text/layout and visual regions
  -> Worker calls VLM for selected visual regions/pages
  -> Worker builds page-level and document-level Markdown
  -> Worker chunks Markdown
  -> Worker stores parse result, Markdown, chunks, and asset metadata
```

Main worker entrypoint:

```text
services/worker/noteflow_worker/pipelines/parse_document.py
```

Main storage locations:

```text
services/api/storage/uploads/
services/api/storage/rendered/
services/api/storage/regions/
PostgreSQL tables
```

## 2. Upload And Runtime File Layout

The uploaded PDF itself is stored as:

```text
services/api/storage/uploads/{document_id}.pdf
```

Rendered page images are stored as:

```text
services/api/storage/rendered/{document_id}/page-XXX.png
```

Cropped visual regions are stored as:

```text
services/api/storage/regions/{document_id}/page-XXX-region-YY.png
```

These runtime files are intentionally ignored by Git. PostgreSQL stores the canonical paths. Do not move UUID-named storage folders manually unless database paths are migrated too.

Relevant storage documentation:

```text
services/api/storage/README.md
```

## 3. Database Output Tables

| Stage | Table | Purpose |
|---|---|---|
| Original document | `documents` | Original PDF metadata, document type, status, content source type, storage path. |
| Worker task | `tasks` | Async parse task progress and error state. |
| Parse summary | `document_parse_results` | Parser name, page count, extracted text length, preview, detected content source type. |
| Page image assets | `document_page_assets` | Rendered page image paths and visual stats. |
| Visual regions | `document_visual_regions` | Cropped/full-page region image paths, bbox, region type. |
| VLM outputs | `document_vlm_results` | VLM transcription, description, LaTeX, code, search text, error message. |
| Layout blocks | `document_layout_blocks` | Normalized text/layout blocks with page number, type, bbox/source asset metadata. |
| Markdown pages | `document_markdown_pages` | One Markdown string per page with quality score and warnings. |
| Markdown document | `document_markdown_documents` | Full document Markdown with page markers and quality report. |
| Chunks | `document_chunks` | Retrieval chunks built from Markdown. |
| Future embeddings | `document_embeddings` | Embedding rows for chunks/other source objects. |

## 4. Document And Content Types

User-facing document types include:

```text
COURSE_NOTES
RESEARCH_PAPER
LECTURE_SLIDES
ASSIGNMENT
PAST_EXAM
HANDWRITTEN_NOTES
OTHER
```

Detected content source types include:

```text
TEXT_PDF
MIXED
SCANNED_PDF
HANDWRITTEN_SCAN
UNKNOWN
```

The detected source type controls the parser route.

The important distinction is:

```text
document_type = user intent and academic structure
content_source_type = detected PDF physical form
```

Routing priority:

1. `HANDWRITTEN_NOTES` forces full-page VLM.
2. `HANDWRITTEN_SCAN` and `SCANNED_PDF` force page-level visual/VLM Markdown.
3. Otherwise the selected `document_type` chooses the academic chunk strategy.
4. `OTHER` uses a conservative mixed fallback.

Strategy resolver:

```text
services/worker/noteflow_worker/pdf/strategies.py
```

Current strategies:

| Type/Condition | Markdown Strategy | Chunk Strategy | Required VLM |
|---|---|---|---:|
| `HANDWRITTEN_NOTES` | `FULL_PAGE_VLM` | `PAGE_AWARE` | Yes |
| `SCANNED_PDF`, `HANDWRITTEN_SCAN` | `PAGE_LEVEL_VISUAL` | `PAGE_AWARE` | Yes |
| `LECTURE_SLIDES` | `SLIDE_LAYOUT` | `SLIDE_AWARE` | No |
| `COURSE_NOTES` | `STRUCTURAL_NOTES` | `TOPIC_AWARE` | No |
| `RESEARCH_PAPER` | `PAPER_SECTIONS` | `PAPER_SECTION_AWARE` | No |
| `ASSIGNMENT`, `PAST_EXAM` | `QUESTION_STRUCTURE` | `QUESTION_AWARE` | No |
| `OTHER` | `MIXED_LAYOUT` | `MIXED_FALLBACK` | No |

## 5. PDF To Markdown: Common Preprocessing

Every parse begins with:

1. Load the `documents` row.
2. Check the PDF exists.
3. Run base PDF text parsing.
4. Detect `content_source_type`.
5. Render every PDF page to PNG.
6. Store page render metadata in `document_page_assets`.

Page render metadata includes:

```text
page_number
image_path
width
height
image_count
drawing_count
image_coverage
text_length
visual_summary
```

The system page number always comes from PDF page order, not visible text printed on the page.

## 6. PDF To Markdown: Scanned And Handwritten PDFs

Used when:

```text
content_source_type in {"SCANNED_PDF", "HANDWRITTEN_SCAN"}
```

Examples:

```text
STAT230Jun1    -> SCANNED_PDF
STAT230Jun17   -> HANDWRITTEN_SCAN
```

Route:

1. Render every page to an image.
2. Create one full-page visual region per page.
3. Region type is:
   - `HANDWRITTEN` for handwritten scans.
   - `FULL_PAGE_VISUAL` for scanned PDFs.
4. Call VLM on every page region.
5. Store VLM results.
6. Convert each VLM page result into a layout block.
7. Build page-level Markdown.
8. Build document-level Markdown.
9. Build page-aware chunks.

Important behavior:

1. VLM analysis is required for this route.
2. If a page-level VLM call times out, the worker retries.
3. If it still fails after retries, the task fails instead of silently producing an empty page.
4. This avoids the previous `STAT230Jun17 page15` failure mode.

The page-level layout block content is:

```text
result.transcription or result.description
```

The layout block metadata marks the source:

```json
{"source":"gemini_page_level_vlm"}
```

## 7. PDF To Markdown: Native Text And Mixed PDFs

Used when:

```text
content_source_type not in {"SCANNED_PDF", "HANDWRITTEN_SCAN"}
```

Examples:

```text
CS136W7, CS136W9, CS136W10 -> TEXT_PDF
CS136MT                    -> MIXED
CS116                      -> TEXT_PDF with important code images
```

Route:

1. Render every page to an image.
2. Analyze page visual statistics.
3. Crop visual regions from embedded image blocks.
4. Filter likely decorative/repeated low-value regions.
5. Add full-page fallback regions when a visually meaningful page would otherwise have no region.
6. Run VLM on selected regions.
7. Extract text/layout blocks with PyMuPDF.
8. Merge layout text and VLM-enriched visual content.
9. Build page-level Markdown.
10. Build document-level Markdown.
11. Build structural Markdown chunks.

## 8. Visual Region Handling

Visual region builder:

```text
services/worker/noteflow_worker/pdf/regions.py
```

Region types include:

```text
CODE_IMAGE
IMAGE
DIAGRAM
HANDWRITTEN
FULL_PAGE_VISUAL
```

Region selection logic:

1. Use PyMuPDF image block bounding boxes when available.
2. Ignore tiny or extreme-aspect-ratio regions.
3. Crop and store region images.
4. Compute image hashes to identify repeated decorative regions.
5. Do not drop `CODE_IMAGE`, `HANDWRITTEN`, or `FULL_PAGE_VISUAL` just because hashes repeat.
6. If a page has meaningful visual content but no final region, create a full-page fallback region.

The full-page fallback fixed the CS116 issue where pages 8, 10, and 11 had code screenshots but no Markdown.

Fallback trigger:

1. Page has visual content.
2. No regions survived filtering.
3. Either image coverage is high enough or image count exists with low native text.

## 8.1 Multi-Modal Region Policy

The parser treats visual content differently depending on semantic value.

| Region / Content | Handling |
|---|---|
| Pure text image | Transcribe with VLM/OCR and insert as Markdown text near its page position. |
| Code screenshot | Classify as `CODE_IMAGE`, ask VLM for transcription and explanation, preserve code fences when possible. |
| Formula image | Ask VLM for transcription and LaTeX; keep formula attached to nearby explanation. |
| Diagram/chart/plot | Ask VLM for labels, relationships, axes, caption-like description, and conceptual interpretation. |
| Handwritten derivation | Use full-page VLM for handwritten documents; preserve derivation order and uncertainty. |
| Decorative/repeated image | Filter when low semantic value, but never drop code/handwritten/full-page fallback only because hashes repeat. |
| Blank or low-content page | Skip or mark low content; do not create misleading chunks. |

The Markdown layer is the source of truth for later chunks and AI notes. If a meaningful image, formula, table, or code screenshot is missing at this stage, retrieval and AI notes cannot reliably recover it.

## 9. VLM Output Handling

VLM provider abstraction:

```text
services/worker/noteflow_worker/vision/providers.py
```

Configured through `.env`:

```text
VISION_PROVIDER=gemini | openai | disabled
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

VLM returns structured fields:

```text
transcription
description
latex
code
uncertainty
search_text
raw_response_json
error_message
```

Retry behavior:

```text
VISION_REQUEST_MAX_ATTEMPTS = 3
VISION_RETRY_BACKOFF_SECONDS = 2.0
```

Retryable errors include:

```text
timeout
HTTP 408/409/429/500/502/503/504
temporary connection failures
```

For page-level scanned/handwritten parsing, VLM errors are fatal after retries. For native text PDFs, a visual region error can be stored while the rest of the text pipeline continues.

## 10. Markdown Construction

Markdown builder:

```text
services/worker/noteflow_worker/pdf/markdown.py
```

Math text normalizer:

```text
services/worker/noteflow_worker/pdf/math_normalizer.py
```

The normalizer handles PDF font artifacts that appear when TeX math glyphs are extracted as private-use Unicode characters. Examples:

```text
 -> \begin{cases} ... \end{cases}
 -> (
 -> )
\x10 and \x11 -> ( and )
```

The system does not blindly delete these characters, because they often represent meaningful math structure such as piecewise functions. It converts them into readable Markdown/LaTeX-ish text before chunking and embedding.

Inputs:

```text
layout_blocks
vlm_results
```

Outputs:

```text
document_markdown_pages
document_markdown_documents
```

Page Markdown construction:

1. Group layout blocks by page.
2. Drop `BOILERPLATE` blocks.
3. Skip VLM results with `error_message`.
4. Render text blocks.
5. Render visual VLM results.
6. Remove duplicate visual text that already exists in native PDF text.
7. Combine page text and visual Markdown.
8. If empty, emit an empty-page marker and warning.
9. Compute source type, warnings, quality score, and structure JSON.

Document Markdown construction:

1. Prefix each page with:

```text
<!-- page:{page_number} -->
```

2. Join pages with Markdown horizontal separators:

```text
---
```

3. Store a document-level quality report.

## 11. Markdown Rendering By Content Type

### Text Blocks

Supported layout block types:

```text
PARAGRAPH
HEADING
LIST
CODE
FORMULA
TABLE
```

Current rendering:

1. `HEADING` becomes a level-2 Markdown heading.
2. Other text blocks are normalized and inserted as Markdown text.
3. Boilerplate blocks are excluded.

### Visual Results

Visual classification uses VLM result fields:

```text
CODE_IMAGE
FORMULA_IMAGE
TABLE_IMAGE
DIAGRAM
TEXT_IMAGE
DECORATIVE_IMAGE
UNKNOWN_VISUAL
```

Rendering behavior:

1. `CODE_IMAGE`: render code/transcription.
2. `FORMULA_IMAGE`: render LaTeX or formula-like transcription.
3. `TABLE_IMAGE`: convert table text to Markdown table when possible.
4. `DIAGRAM`: render visible text plus explanation.
5. `TEXT_IMAGE`: render transcription.
6. `DECORATIVE_IMAGE`: filter out.
7. `FULL_PAGE_VISUAL` and `HANDWRITTEN`: use full transcription as the page content.

## 12. Markdown To Chunk: Parser

Chunk builder:

```text
services/worker/noteflow_worker/pdf/layout.py
```

Entrypoint:

```python
build_markdown_chunks(
    markdown_text,
    layout_blocks,
    vlm_results,
    asset_ids_by_page,
    content_source_type,
    document_type,
    chunk_strategy
)
```

The first step parses document Markdown into `MarkdownElement` objects:

```text
content
block_type
page_number
heading_path
```

Markdown element types:

```text
HEADING
PARAGRAPH
CODE
FORMULA
TABLE
MIXED_VISUAL
```

Page markers are read from:

```text
<!-- page:X -->
```

## 13. Markdown To Chunk: Native Text And Mixed PDFs

Used when the document is not page-level scanned/handwritten.

Token parameters:

```text
TARGET_TOKENS = 450
MAX_TOKENS = 800
```

Rules:

1. Aggregate adjacent Markdown elements.
2. Flush a chunk when section changes.
3. Flush when adding an element would exceed max tokens.
4. Flush after target tokens when crossing a page boundary.
5. Long table/code/formula blocks can be standalone.
6. Small table/formula/code snippets stay with nearby context.
7. Add semantic overlap for paragraph/mixed chunks.

Standalone behavior:

```text
TABLE standalone only if token_count >= 120
CODE standalone only if token_count >= 120
FORMULA standalone only if token_count >= 120
MIXED_VISUAL always standalone
```

This prevents mathematical expressions such as:

```text
P(X > s+t | X > s) = P(X > t)
```

from being incorrectly isolated as tiny table chunks.

## 14. Markdown To Chunk: Scanned And Handwritten PDFs

Used when:

```text
content_source_type in {"SCANNED_PDF", "HANDWRITTEN_SCAN"}
```

Also used when all VLM results are full-page visual/handwritten regions.

Page-aware parameters:

```text
PAGE_AWARE_MIN_MERGE_TOKENS = 60
PAGE_AWARE_TARGET_TOKENS = 650
PAGE_AWARE_MAX_TOKENS = 1000
```

Rules:

1. Keep same-page content together by default.
2. Skip empty page markers.
3. Merge adjacent short pages if they appear to be the same topic.
4. Start a new chunk when a new page begins with a topic heading and current chunk has enough content.
5. Split only if a single page exceeds the page-aware max.
6. Do not add semantic overlap.

Why no semantic overlap:

For page-aware handwritten/scanned chunks, overlap can make `page_start` disagree with the first text in the chunk. The system prefers clean page provenance.

Topic heading heuristic:

1. Short uppercase lecture headings.
2. Keywords such as:

```text
theorem
proof
example
definition
lemma
corollary
roadmap
notes
setup
```

## 15. Chunk Metadata

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
  "headings": [],
  "blockTypes": ["PARAGRAPH", "TABLE"],
  "containsImage": false,
  "containsTable": true,
  "containsFormula": false,
  "containsCode": false,
  "assetIds": [],
  "bboxRefs": []
}
```

The chunk's `assetIds` link it back to page render assets or visual assets for frontend source inspection.

## 16. Long Text PDF Guardrails

Long native-text PDFs, such as full course-note books, need different cost controls from short slide decks.

Current guardrails:

1. The worker still renders page assets so the frontend can cite and inspect source pages.
2. OCR is skipped when a page already has enough native PDF text.
3. OCR is used only for low-native-text pages or pages with meaningful image coverage.
4. Visual regions are still saved for inspection.
5. For long non-handwritten documents, only high-value regions are sent to VLM:
   - code screenshots
   - handwritten regions
   - low-text visual pages
   - substantial diagrams
6. Ordinary full-page visual fallback regions are not sent to VLM in long text PDFs.
7. Handwritten/scanned documents are not affected by this shortcut; they still require full-page VLM when selected by strategy.

This prevents a 300+ page text PDF from spending minutes on low-value OCR/VLM work before the main Markdown and chunk output is available.

## 17. Current Output Locations

### Filesystem

| Output | Location |
|---|---|
| Uploaded PDF | `services/api/storage/uploads/{document_id}.pdf` |
| Page render | `services/api/storage/rendered/{document_id}/page-XXX.png` |
| Visual crop | `services/api/storage/regions/{document_id}/page-XXX-region-YY.png` |
| Runtime storage README | `services/api/storage/README.md` |

### PostgreSQL

| Output | Location |
|---|---|
| Parse summary | `document_parse_results` |
| Page render metadata | `document_page_assets` |
| Visual region metadata | `document_visual_regions` |
| VLM analysis | `document_vlm_results` |
| Layout blocks | `document_layout_blocks` |
| Page Markdown | `document_markdown_pages` |
| Full Markdown | `document_markdown_documents` |
| Chunks | `document_chunks` |

Important point:

Markdown and chunks are not stored as loose `.md` or `.json` files in the runtime pipeline. They are stored in PostgreSQL. The filesystem only stores binary/source assets such as PDFs and PNGs.

## 17. Repair And Backfill Scripts

Useful worker scripts:

| Script | Purpose |
|---|---|
| `services/worker/scripts/reprocess_documents.py` | Re-run full parse pipeline for selected document IDs. |
| `services/worker/scripts/rebuild_chunks.py` | Rebuild chunks from existing stored Markdown without re-calling VLM. |
| `services/worker/scripts/backfill_markdown.py` | Rebuild Markdown from existing layout/VLM records. |
| `services/worker/scripts/retry_failed_vlm.py` | Retry failed VLM regions/pages and rebuild Markdown/chunks. |

## 18. Known Limitations

1. Footer and slide furniture cleanup still needs improvement for CS136-style decks.
2. Math Markdown is usable for search but not fully reliable for exact symbolic QA.
3. Table reconstruction is heuristic.
4. Native text PDFs can still produce broad chunks if section headings are weak.
5. Code snippets are often function-level chunks, which is good for search but can increase tiny chunk counts.

## 19. Current Recommendation

The current pipeline is good enough to proceed with:

1. Embedding generation.
2. Search endpoint.
3. Frontend retrieval inspection.
4. Source-grounded AI question answering prototype.

Before calling the RAG system production-ready, improve:

1. Footer/boilerplate filtering.
2. Math formula normalization.
3. Chunk quality flags in frontend.
4. Retrieval evaluation on real user questions.
