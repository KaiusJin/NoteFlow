# NoteFlow Chunking Strategy

This document describes the current PDF chunking strategy, why it is designed this way, and the current quality assessment based on a parsed lecture PDF.

Document-type-specific routing is defined in:

[DOCUMENT_TYPE_PROCESSING_STRATEGY.md](/Users/kaius/Project/NoteFlow/docs/technical/DOCUMENT_TYPE_PROCESSING_STRATEGY.md)

## 1. Goal

The chunker should produce text chunks that are useful for:

1. Semantic search.
2. Embedding generation.
3. RAG answers.
4. Citation grounding.
5. AI note generation.
6. Frontend source inspection.

The system should be conservative. It should avoid deleting technical content, especially code, formulas, table rows, numbered items, and exercise text.

## 2. Current Pipeline

```text
PDF
  -> render each page as an image asset
  -> extract native PDF text blocks with PyMuPDF coordinates
  -> run local OCR on rendered pages when visual content is detected
  -> crop visual regions from image blocks or full-page fallback
  -> send cropped regions to Gemini/OpenAI-compatible VLM provider
  -> normalize text, formula, code, list, table, boilerplate, and visual blocks
  -> save layout blocks with page number, bbox, type, metadata, and asset references
  -> build structural and semantic chunks from layout blocks
  -> save chunk metadata for retrieval and source grounding
```

The implementation lives in:

[parser.py](/Users/kaius/Project/NoteFlow/services/worker/noteflow_worker/pdf/parser.py)
[layout.py](/Users/kaius/Project/NoteFlow/services/worker/noteflow_worker/pdf/layout.py)
[visual.py](/Users/kaius/Project/NoteFlow/services/worker/noteflow_worker/pdf/visual.py)
[regions.py](/Users/kaius/Project/NoteFlow/services/worker/noteflow_worker/pdf/regions.py)
[providers.py](/Users/kaius/Project/NoteFlow/services/worker/noteflow_worker/vision/providers.py)

## 3. Page Numbers

System page numbers come from the PDF reader page order:

```python
for index, page in enumerate(reader.pages, start=1):
```

They are not extracted from visible text such as:

```text
1/52
Page 3
```

Visible page labels may appear in extracted text, but they are not trusted as system page numbers.

Each chunk stores:

```text
page_number
page_start
page_end
```

`page_number` is currently the same as `page_start` for compatibility with earlier code.

## 4. Content Source Detection

Before chunking, the parser estimates whether the PDF has extractable text.

Current output types:

```text
TEXT_PDF
SCANNED_PDF
HANDWRITTEN_SCAN
MIXED
UNKNOWN
```

If the file appears to be scanned or handwritten and has very little extracted text, local OCR and visual page rendering are still available through the layout pipeline. The current system does not pretend OCR is perfect; OCR-derived blocks are marked with confidence and `vlmStatus`.

## 5. Line Classification

Native PDF text is extracted into coordinate-aware layout blocks. Lines inside those blocks are classified before chunking.

Current line types:

```text
CODE
FORMULA
TABLE
LIST
HEADING
PARAGRAPH
BOILERPLATE
```

The purpose is not cosmetic. The line type helps the system decide how content should be chunked, embedded, displayed, and later cited.

## 6. Code Handling

Code-like lines are protected from aggressive normalization and arbitrary splitting.

Examples of code signals:

```text
struct
class
def
return
malloc
printf
->
//
/*
*/
;
{ }
```

The parser tries to preserve code with nearby explanatory text. A chunk may be labeled `CODE` if code is the dominant block type inside that chunk.

## 7. Formula Handling

Formula-like lines are also protected.

Examples of formula signals:

```text
\sum
\int
\frac
E[X]
Var(X)
P(A)
≤ ≥
∑ ∫
Greek symbols
compact expressions with = or ^
```

The parser does not delete math-looking lines as if they were page numbers or footers.

## 8. Table Handling

Table-like lines are marked separately when possible.

Signals include:

1. Multiple pipe separators.
2. Multiple large spacing-separated columns.
3. Multiple complexity terms such as `O(1)` and `O(n)`.

When a block looks like a table, it is converted into Markdown table syntax before embedding/chunking. This is not a full table parser yet, but it is better than flattening columns into plain text.

Future improvement: replace the heuristic table detector with a real table extraction path for ruled tables and multi-page tables.

## 9. Boilerplate Detection

The parser does not delete text using a single footer regex.

Instead, it marks boilerplate only when a line is:

1. Near the top or bottom of a page.
2. Short enough to plausibly be page furniture.
3. Not code, formula, or table content.
4. Repeated across multiple pages after normalization.

Normalization replaces numbers with `#`, so repeated families such as:

```text
1/52 CS 136 - Winter 2026 Section 9, Part 1:
2/52 CS 136 - Winter 2026 Section 9, Part 1:
```

can be detected as related without trusting the visible page label as the real page number.

The design principle is:

```text
mark repeated page furniture, but do not blindly delete technical-looking text
```

There are now two boilerplate layers:

1. Line-level boilerplate for native text lines.
2. Layout-level boilerplate for repeated short coordinate blocks such as slide headers and page counters.

Layout-level boilerplate is saved in `document_layout_blocks` as `BOILERPLATE`, but excluded from final chunks.

## 10. Heading And Section Detection

Heading detection is intentionally conservative.

A heading is usually:

1. Short.
2. Not code.
3. Not formula.
4. Not a list item.
5. Not a visible page label.
6. Title-like or colon-terminated.

Examples from the current lecture PDF:

```text
Linked lists: traversal
Functional vs Imperative approach
Mixing paradigms
Node sharing
```

The heading becomes the current `section_title` for following blocks.

## 11. Layout Blocks

Before chunking, the system saves normalized blocks to:

```text
document_layout_blocks
```

Each block stores:

```text
document_id
page_number
block_index
block_type
content
bbox_json
section_title
heading_path_json
source_asset_id
confidence
metadata_json
```

Current block types include:

```text
PARAGRAPH
HEADING
LIST
CODE
FORMULA
TABLE
MIXED_VISUAL
IMAGE
BOILERPLATE
```

The blocks are returned by:

```text
GET /documents/{documentId}/layout-blocks
```

## 12. Visual, OCR, And VLM Handling

Every page is rendered to a PNG page asset. The system stores those assets in:

```text
document_page_assets
```

For visual-heavy pages, the system crops visual regions from PDF image blocks. If no reliable image block is found, the system falls back to the full rendered page.

Region types include:

```text
CODE_IMAGE
IMAGE
DIAGRAM
HANDWRITTEN
FULL_PAGE_VISUAL
```

Each cropped region is saved to:

```text
document_visual_regions
```

Each region can be analyzed by a VLM provider:

```text
VISION_PROVIDER=gemini | openai | disabled
```

The VLM result is saved to:

```text
document_vlm_results
```

For visual-heavy pages, the system creates `MIXED_VISUAL` blocks and chunks that include:

1. Embedded image count.
2. Vector drawing count.
3. Estimated image coverage.
4. VLM transcription.
5. VLM description.
6. LaTeX or code when available.
7. Search text for embedding/retrieval.
8. Local OCR fallback when VLM is unavailable.
9. A source asset reference for frontend inspection.

Current visual metadata includes:

```json
{
  "source": "vlm_region_analysis",
  "containsImage": true,
  "containsDrawing": true,
  "imageCoverage": 0.42,
  "ocrAvailable": true,
  "vlmStatus": "completed",
  "vlmProvider": "gemini",
  "vlmModel": "gemini-2.5-flash",
  "regionType": "CODE_IMAGE"
}
```

The provider abstraction also supports OpenAI-compatible vision calls. API keys are read from local `.env` and are not stored in the database or returned by API responses.

## 13. Chunk Construction

The parser builds typed Markdown elements first, then aggregates them into chunks. There are two chunking paths.

### 13.1 Native Text / Mixed PDFs

Native text PDFs and mixed PDFs use structural Markdown chunking.

Current token parameters:

```text
MIN_TOKENS = 80
TARGET_TOKENS = 450
MAX_TOKENS = 800
```

Rules:

1. Small adjacent blocks may be merged.
2. A chunk can cross page boundaries.
3. A new section can trigger a chunk boundary.
4. Code and formula blocks are not aggressively split.
5. Large visual blocks can become standalone chunks.
6. Large table blocks can become standalone chunks.
7. Small table-like or formula-like elements stay with nearby explanation.
8. Heading-only chunks are merged into the following semantic block.
9. Multi-region visual pages are split into region-level visual chunks to avoid oversized chunks.
10. Token count is estimated, not produced by a model-specific tokenizer.

The important change is that table-like text is no longer always standalone. This avoids breaking math such as:

```text
P(X > s+t | X > s) = P(X > t)
```

into a tiny context-free `TABLE` chunk.

### 13.2 Scanned / Handwritten PDFs

`SCANNED_PDF` and `HANDWRITTEN_SCAN` use page-aware lecture chunking because their Markdown normally comes from page-level VLM transcription.

Current page-aware token parameters:

```text
PAGE_AWARE_MIN_MERGE_TOKENS = 60
PAGE_AWARE_TARGET_TOKENS = 650
PAGE_AWARE_MAX_TOKENS = 1000
```

Rules:

1. Content from the same page is kept together by default.
2. Short adjacent pages can be merged when they are part of the same topic.
3. Clear new page topics, such as theorem/example/proof headings or short uppercase lecture headings, trigger a boundary once the current chunk has enough content.
4. Empty pages are skipped instead of being merged into unrelated chunks.
5. Very large single pages are split only when they exceed the page-aware max token limit.
6. Page-aware chunks do not receive semantic overlap, because overlap can make `page_start` disagree with the chunk text.

The token estimate is:

```text
max(len(text.split()) * 1.3, len(text) / 4)
```

## 14. Saved Chunk Fields

Each chunk currently stores:

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

These fields are returned by:

```text
GET /documents/{documentId}/chunks
```

Important metadata fields:

```json
{
  "headings": ["CLASSES: MAGIC METHODS", "QUESTION: BANK ACCOUNT"],
  "blockTypes": ["HEADING", "PARAGRAPH", "LIST"],
  "containsImage": false,
  "containsTable": false,
  "containsFormula": false,
  "containsCode": false,
  "assetIds": [],
  "bboxRefs": [
    {"page": 14, "bbox": [42.77, 35.99, 223.6, 63.64], "type": "HEADING"}
  ]
}
```

## 15. Preview Generation

`document_parse_results.extracted_text_preview` is not an AI summary.

It is a compact preview generated from the cleaned first chunks:

```text
first few cleaned chunks -> whitespace compacted -> first 600 characters
```

This avoids showing repeated page footers in the preview.

## 16. Current Quality Check

After applying the current strategy to all uploaded Markdown-backed documents, `STAT230Jun17` changed from:

```text
old chunks: 14
old median tokens: 29.5
old tiny chunks under 80 tokens: 11
old issue: formula/table-like lines were split into context-free chunks
```

to:

```text
new chunks: 6
new min tokens: 61
new max tokens: 232
new tiny chunks under 80 tokens: 2
new behavior: page-level formulas, small tables, and explanations stay together
```

This is more appropriate for handwritten lecture notes. The remaining small chunks correspond to short self-contained topics rather than accidental formula splits.

## 17. Known Limitations

Current limitations:

1. `chunk_type` is chunk-level, not block-level. A mixed explanation/code chunk may be labeled `CODE`.
2. Some slide titles or exercise labels may still be imperfectly identified.
3. Token counts are estimates, not exact tokenizer counts.
4. Table detection is still heuristic and not a full table reconstruction engine.
5. Page-aware topic detection is conservative and may still merge short adjacent topics when headings are unclear.
6. Scanned and handwritten quality depends on VLM transcription quality before chunking.

These limitations are acceptable for the current phase because the generated chunks are already suitable for the next step: embeddings and semantic search.

## 18. Future Improvements

Useful next improvements:

1. Store block-level metadata in a separate table or `metadata_json`.
2. Add model-specific tokenization before embedding.
3. Add chunk quality metrics in the frontend.
4. Add an evaluation set of PDFs covering lecture slides, papers, textbooks, code-heavy notes, math-heavy notes, and scanned notes.
5. Replace heuristic table detection with layout-aware table extraction.
6. Add optional semantic boundary scoring before merging adjacent short pages.

## 19. Current Assessment

The current strategy is reasonable for the project stage.

It is conservative, page-aware, code-aware, formula-aware, and citation-friendly. It avoids the dangerous behavior of deleting lines based on one regex. It also produces chunks with manageable size and useful metadata.

The main next step should be embedding and semantic search, while keeping chunk quality evaluation visible as more document types are tested.
