# Document Type Processing Strategy

This document defines how NoteFlow should use `document_type` when converting PDFs to Markdown and chunks.

This is a supporting reference. The consolidated current specification is:

```text
docs/technical/NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md
```

The important design rule is:

```text
document_type = user intent and academic structure
content_source_type = detected PDF physical form
```

The pipeline should use both. `document_type` decides what structure we expect, while `content_source_type` corrects the plan when the file is physically scanned, handwritten, or mixed.

## 1. Document Types

The supported product-level document types are:

```text
COURSE_NOTES
LECTURE_SLIDES
RESEARCH_PAPER
ASSIGNMENT
PAST_EXAM
HANDWRITTEN_NOTES
OTHER
```

`TEXTBOOK_CHAPTER` is not supported for now. It is too broad and overlaps with `COURSE_NOTES`, `RESEARCH_PAPER`, and `OTHER`. If textbook support becomes important later, it should return with a clear strategy for chapters, exercises, margin notes, figures, and references.

## 2. Routing Priority

The routing decision follows this priority:

1. If `document_type == HANDWRITTEN_NOTES`, force full-page VLM Markdown.
2. Else if `content_source_type == HANDWRITTEN_SCAN`, use full-page VLM Markdown.
3. Else if `content_source_type == SCANNED_PDF`, use page-level visual OCR/VLM Markdown.
4. Else use the strategy for the selected `document_type`.
5. If the selected type is `OTHER`, use a conservative mixed-document fallback.

This avoids a common failure mode: the user chooses "Lecture slides", but the uploaded PDF is actually a scan. In that case the detected source type must still push the document through visual parsing.

## 3. Strategy Matrix

| Document Type | Markdown Strategy | VLM Strategy | Chunk Strategy |
|---|---|---|---|
| `HANDWRITTEN_NOTES` | Full-page visual transcription per page. Preserve page order and formulas. | Required for every page. Failure should retry, then fail the parse instead of producing empty content. | Page-aware chunks. Keep each page intact unless a page is too large. Merge short adjacent pages only when they continue the same topic. |
| `LECTURE_SLIDES` | Slide/page-aware Markdown. Use native text first, bind visual regions into their slide position. Remove repeated headers/footers only when safely identified. | Selective. Use VLM for code screenshots, diagrams, handwritten annotations, dense images, and pages with weak native text. | Slide-aware chunks. Prefer 1 to 3 slides per chunk. Do not split a slide unless it exceeds the max token budget. |
| `COURSE_NOTES` | Structural Markdown with headings, definitions, theorems, examples, proofs, formulas, tables, and code preserved. | Selective fallback for diagrams, handwritten inserts, scanned regions, and missing text pages. | Topic-aware chunks. Prefer theorem/example/proof/solution units, with formulas attached to surrounding explanation. |
| `RESEARCH_PAPER` | Paper-section Markdown: title, abstract, introduction, methods, results, discussion, conclusion, references, figures, tables. | Selective for figures, plots, equations rendered as images, and scanned pages. | Section-aware chunks. Keep abstract standalone; bind figure/table captions to nearby discussion; references should be separate and low priority. |
| `ASSIGNMENT` | Problem Markdown: instructions, question numbers, subparts, starter code, examples, constraints, submission notes. | Selective for screenshots, handwritten annotations, and code images. | Question-aware chunks. Prefer one question or one subpart per chunk. Keep starter code and examples with the relevant question. |
| `PAST_EXAM` | Exam Markdown: question numbers, subparts, marks, formula sheets, answer space, diagrams. | Selective for scanned exams, handwritten notes, diagrams, and formula sheets. | Question-aware chunks. Prefer one question per chunk. Preserve marks and page provenance. Formula sheets can be standalone chunks. |
| `OTHER` | Conservative mixed Markdown. Preserve page order and typed blocks. | Selective based on visual density and missing native text. | Conservative page/section chunks with larger token budget and minimal assumptions. |

Implementation mapping:

| Condition | `markdown_strategy` | `chunk_strategy` | `force_full_page_vlm` | `require_vlm_success` |
|---|---|---|---:|---:|
| `HANDWRITTEN_NOTES` | `FULL_PAGE_VLM` | `PAGE_AWARE` | true | true |
| `SCANNED_PDF` / `HANDWRITTEN_SCAN` | `PAGE_LEVEL_VISUAL` | `PAGE_AWARE` | true | true |
| `LECTURE_SLIDES` | `SLIDE_LAYOUT` | `SLIDE_AWARE` | false | false |
| `COURSE_NOTES` | `STRUCTURAL_NOTES` | `TOPIC_AWARE` | false | false |
| `RESEARCH_PAPER` | `PAPER_SECTIONS` | `PAPER_SECTION_AWARE` | false | false |
| `ASSIGNMENT` / `PAST_EXAM` | `QUESTION_STRUCTURE` | `QUESTION_AWARE` | false | false |
| `OTHER` | `MIXED_LAYOUT` | `MIXED_FALLBACK` | false | false |

Code:

```text
services/worker/noteflow_worker/pdf/strategies.py
```

## 4. Markdown Requirements

The Markdown layer is the source of truth for chunking. Chunking should not try to recover structure that the Markdown conversion failed to represent.

Every Markdown page should preserve:

1. PDF page number from the PDF reader, not visible page labels.
2. Reading order.
3. Headings or topic labels when available.
4. Code fences for code.
5. LaTeX-style blocks for equations when available.
6. Markdown tables for structured tables when confidence is high.
7. Image, diagram, and handwritten descriptions close to their original page position.
8. VLM/OCR failure markers only when the parse fails visibly; silent empty pages are not acceptable.

## 5. Chunk Requirements

Chunks should optimize retrieval quality, not only token size.

Each chunk should contain:

```text
content
page_start
page_end
chunk_type
token_count
section_title
metadata_json
```

`metadata_json` should include:

```json
{
  "documentType": "LECTURE_SLIDES",
  "contentSourceType": "TEXT_PDF",
  "chunkStrategy": "SLIDE_AWARE",
  "headings": ["Stacks", "Example"],
  "blockTypes": ["HEADING", "CODE", "PARAGRAPH"],
  "containsImage": true,
  "containsFormula": false,
  "containsCode": true,
  "assetIds": [],
  "bboxRefs": []
}
```

The current implementation attaches this metadata in `document_chunks.metadata_json` through `chunk_from_elements(...)`.

## 6. Chunk Strategy Types

The implementation should expose the selected chunk strategy in metadata:

```text
PAGE_AWARE
SLIDE_AWARE
TOPIC_AWARE
PAPER_SECTION_AWARE
QUESTION_AWARE
MIXED_FALLBACK
```

These labels are useful for debugging, frontend inspection, and future retrieval evaluation.

## 7. Token Budgets

Recommended budgets:

| Strategy | Target Tokens | Max Tokens | Notes |
|---|---:|---:|---|
| `PAGE_AWARE` | 650 | 1000 | Used for scanned and handwritten documents. |
| `SLIDE_AWARE` | 700 | 1100 | Slides are often short; merge adjacent slides only when coherent. |
| `TOPIC_AWARE` | 650 | 1000 | Good for math/course notes. |
| `PAPER_SECTION_AWARE` | 800 | 1200 | Papers need slightly larger context windows. |
| `QUESTION_AWARE` | 750 | 1200 | Assignments/exams should keep full question context. |
| `MIXED_FALLBACK` | 650 | 1000 | Conservative default. |

The max token value is a hard split threshold. The target token value is only a merge guideline.

## 8. Type-Specific Boundaries

### Handwritten Notes

Boundary rules:

1. Keep each page intact by default.
2. Merge short pages when the next page does not start a clear new topic.
3. Split a page only when it exceeds the max token budget.
4. Do not add semantic overlap, because duplicated handwritten content can confuse citations.

### Lecture Slides

Boundary rules:

1. Preserve slide/page boundary.
2. Keep code screenshots and diagrams with the slide where they appear.
3. Merge short consecutive slides when they share the same heading/topic.
4. Split only when one slide is too large.

### Course Notes

Boundary rules:

1. Prefer natural academic units: definition, theorem, lemma, proof, example, solution.
2. Keep formulas with the explanation immediately before or after them.
3. Keep code with its explanation.
4. Allow chunks to cross page boundaries when the same topic continues.

### Research Papers

Boundary rules:

1. Keep abstract standalone.
2. Chunk by section/subsection.
3. Bind figures and tables to captions and nearby references.
4. Keep references separate from the main semantic chunks.

### Assignments And Past Exams

Boundary rules:

1. Prefer one question per chunk.
2. Split by subpart only when a question is too large.
3. Keep marks, constraints, starter code, examples, and diagrams with the question.
4. Keep formula sheets as standalone chunks.

## 9. Failure Policy

The parser should not silently advance after producing empty important content.

Required behavior:

1. VLM calls retry with backoff.
2. Full-page VLM failures for handwritten/scanned documents fail the parse after retries.
3. Selective VLM failures for native PDFs can fall back to OCR/native text, but the Markdown should include a visible low-confidence marker in metadata.
4. Empty Markdown pages with non-empty page images should be flagged.
5. Chunking should never treat an empty failed page as normal content.

## 10. Implementation Status

Implemented:

1. Removed `TEXTBOOK_CHAPTER` from API and frontend document type options.
2. Added a worker-side document processing strategy resolver.
3. Passed the resolved strategy into chunk generation.
4. Forced `HANDWRITTEN_NOTES` through full-page VLM even when native text exists.
5. Added chunk metadata fields for `documentType`, `contentSourceType`, and `chunkStrategy`.
6. Added first-pass strategy-specific chunk boundaries for slides, course notes, papers, assignments, and exams.
7. Added full-page VLM failure policy for scanned and handwritten routes.
8. Added visual fallback for meaningful pages that would otherwise lose code/images.
9. Added AI notes resume and offline Markdown rebuild outside this strategy layer.

Still needed after code deployment:

1. Add frontend filtering/inspection for `chunkStrategy`.
2. Add evaluation metrics grouped by `document_type`.
3. Improve source citation precision for small single-group documents.
