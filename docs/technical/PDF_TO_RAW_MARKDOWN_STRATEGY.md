# PDF to Raw Markdown: Pre-V2 Strategy Audit

Audit date: 2026-06-27

> Historical note: this document records the code audit and gap analysis that
> preceded the Converter V2 rewrite. For the current implementation and
> operational specification, `PDF_CONVERTER_V2_ARCHITECTURE.md` is
> authoritative; this document is kept to explain why the rewrite happened.

This document described the implementation as it existed at audit time — how
NoteFlow converted PDFs into raw Markdown — focusing on plain text, formulas,
images containing important text, useless images, and handwriting. It
describes **behavior at the time**, not the target design.

## 1. Scope and Output Definition

Raw Markdown here means the two-level result the worker produced during
parsing, before any AI Notes rewriting:

- `document_markdown_pages`: per-page Markdown with `source_type`, a quality
  score, and warnings.
- `document_markdown_documents`: the full Markdown stitched page by page,
  with `<!-- page:N -->` and `---` between pages.

These were subsequently cut into `document_chunks`. The main call chain:

```text
ParseDocumentPipeline.run
  -> parse_pdf                         # extract base text, classify physical source
  -> resolve_processing_strategy       # choose full-page VLM vs text/visual hybrid
  -> analyze_pdf_visuals               # render PNG per page, count images/drawings,
                                       # conditionally run local OCR
  -> [full-page VLM] or [cropped regions + selective VLM + PyMuPDF layout]
  -> build_markdown_document           # per-page and full-document raw Markdown
  -> build_markdown_chunks             # retrieval chunks from raw Markdown
```

Key implementation files:

- `services/worker/noteflow_worker/pipelines/parse_document.py`
- `services/worker/noteflow_worker/pdf/parser.py`
- `services/worker/noteflow_worker/pdf/strategies.py`
- `services/worker/noteflow_worker/pdf/visual.py`
- `services/worker/noteflow_worker/pdf/regions.py`
- `services/worker/noteflow_worker/pdf/layout.py`
- `services/worker/noteflow_worker/pdf/markdown.py`
- `services/worker/noteflow_worker/vision/providers.py`

## 2. Overall Routing

### 2.1 Physical source-type classification

`parse_pdf()` extracted text with pypdf, then classified by total characters
divided by page count:

| Condition | `content_source_type` |
|---|---|
| 0 pages | `UNKNOWN` |
| User type `HANDWRITTEN_NOTES` and < 100 chars/page | `HANDWRITTEN_SCAN` |
| < 100 chars/page | `SCANNED_PDF` |
| < 300 chars/page | `MIXED` |
| ≥ 300 chars/page | `TEXT_PDF` |

This was a document-level average, not per-page classification. A sparse
native-text PDF could be treated as a scan; a mostly-text PDF with a few
scanned pages could land in `MIXED` or `TEXT_PDF` as a whole.

### 2.2 The two actual processing routes

| Trigger | Markdown route | VLM requirement |
|---|---|---|
| User type `HANDWRITTEN_NOTES` | Full-page VLM per page | Every page must succeed |
| Source `SCANNED_PDF` or `HANDWRITTEN_SCAN` | Full-page VLM per page | Every page must succeed |
| Everything else | PyMuPDF native text + cropped-region VLM | Regions may fail; the text flow continues |

The `markdown_strategy` names for `LECTURE_SLIDES`, `COURSE_NOTES`,
`RESEARCH_PAPER`, `ASSIGNMENT`, etc. were mostly recorded in strategy and
metadata. Beyond the full-page-VLM vs hybrid fork, they mainly influenced
later chunk boundaries rather than selecting different PDF-to-Markdown
engines.

## 3. Strategy at the Time for Five Content Classes

### 3.1 Plain text

Applied to `TEXT_PDF` or `MIXED` pages with a reliable text layer.

Steps:

1. PyMuPDF read text blocks and bboxes via `page.get_text("dict", sort=True)`.
2. Spans were joined per line; control characters and some math private-use
   glyphs were cleaned.
3. Each line was classified heuristically as `CODE`, `FORMULA`, `TABLE`,
   `LIST`, `HEADING`, or `PARAGRAPH`.
4. Small paragraphs were merged; headings maintained a simple heading path.
5. Ordinary paragraphs/headings repeated on at least 3 pages and shorter than
   ~35 tokens could be marked `BOILERPLATE` and excluded from Markdown.
6. Headings rendered uniformly as `##`; paragraphs kept the extracted text.

Priority: native text was the primary content source of the hybrid route. It
was not polished by any LLM, so ordering, line breaks, and symbol quality
depended on the PDF text layer and PyMuPDF block ordering.

Limitations at the time:

- Multi-column pages sorted by `(y, x, block_index)`; complex layouts could
  produce wrong reading order.
- Boilerplate filtering used only cross-page fingerprints of short text; it
  did not strictly use header/footer coordinates.
- `LIST` degraded to plain paragraphs on some classification/merge paths;
  Markdown list syntax was not guaranteed.

### 3.2 Formulas

Formulas came from two sources.

#### A. Text-layer formulas

`is_formula_like()` recognized formulas via `\sum`, `\int`, `\frac`, common
math symbols, equals signs, exponents, and short-expression rules. Blocks
classified as formulas rendered as:

```markdown
$$
raw extracted text
$$
```

`math_normalizer.py` performed only limited repairs:

- replaced a few control characters;
- repaired specific private-use glyphs into brackets or `\begin{cases}`;
- appended missing `\end{cases}` terminators;
- collapsed whitespace.

This was not full math OCR and did not reliably convert every PDF glyph into
LaTeX. `$$` blocks could therefore still contain flattened text, wrong
characters, or scrambled expressions.

#### B. Formulas inside images

Cropped regions went to the VLM; the structured result contained
`transcription` and `latex`. If the Markdown layer detected `latex` or
formula features, it classified the region `FORMULA_IMAGE` and preferred
wrapping `latex` in `$$`.

One important branch existed: whenever a transcription exceeded 12 words,
`render_visual_result()` returned the transcription early — even when the VLM
had provided `latex`, it might never reach the final Markdown. Dense formula
images were therefore not guaranteed to keep structured LaTeX.

Formula dedupe: if an image formula overlapped the page's native text by
~70% of normalized tokens, or reached 0.82 string similarity, the visual
version was filtered to avoid duplicates.

### 3.3 Images containing important text

Typical content: screenshots, scanned text, code screenshots, chart labels,
annotated diagrams.

#### Region discovery

1. Every page rendered to PNG at 144 DPI.
2. PDF image-block bboxes read.
3. Candidates dropped if under 3% of page area, smaller than 40×40 px, or
   with aspect ratio beyond 12:1 / 1:12.
4. If a page had visual content but no usable crop, a full-page region was
   added when either:
   - image coverage ≥ 12%; or
   - the page had images and native text ≤ 160 chars.

Pages with ≥ 8 vector drawings also counted as "having visual content"; with
no image blocks they took the full-page fallback.

#### VLM extraction

The VLM was required to return fixed JSON fields:

```text
transcription, description, latex, code, uncertainty, search_text
```

The prompt demanded exact transcription of visible text/handwriting/code/
labels, `[unclear]` for unreadable spans, and explanations of figures,
arrows, axes, and relationships. Gemini and OpenAI providers were supported.

VLM results were then classified:

| Class | Raw Markdown handling |
|---|---|
| `TEXT_IMAGE` | Insert the transcription |
| `CODE_IMAGE` | Fenced code block where possible |
| `FORMULA_IMAGE` | Prefer `$$...$$`, subject to the long-transcription branch above |
| `TABLE_IMAGE` | Guess columns by `\|` or runs of spaces, convert to a Markdown table |
| `DIAGRAM` / `UNKNOWN_VISUAL` | `<figure>` with visible text, explanation, LaTeX, and uncertainty |
| Full-page visual / handwriting | The page transcription becomes the page body |

Visual transcriptions duplicating native text were filtered by token overlap
or string similarity; regions on the same page deduped against each other by
containment or 0.82 similarity.

#### Long documents and count caps

- At most `VISION_MAX_REGIONS_PER_DOCUMENT` regions per document (default 24
  at the time), stopping in page order once the cap was reached.
- Non-handwritten documents past 120 pages selected at most 8 high-value
  regions for VLM calls. High-value meant `CODE_IMAGE`, `HANDWRITTEN`,
  low-native-text pages, and `DIAGRAM` with image coverage ≥ 12%.

Ordinary text images in long documents could therefore go untranscribed.

### 3.4 Useless images

Two filter layers existed.

#### A. Pre-VLM: repeated-region filtering

Each crop computed an 8×8 grayscale average hash. A region became a
repetition candidate only when the identical hash appeared strictly more than
`max(3, ceil(pages × 15%))` times. Only ordinary regions on pages with
`imageCoverage < 12%` could be deleted.

The following types were never deleted for repetition alone:

- `CODE_IMAGE`
- `HANDWRITTEN`
- `FULL_PAGE_VISUAL`

Note: this used exact average-hash equality, not a distance threshold, and
the metadata stored page-level image coverage rather than the crop's own area
ratio. It filtered some logos/backgrounds but was not reliable semantic
useless-image detection.

#### B. Post-VLM: decorative filtering

If there was no transcription, and the VLM text was empty or at most two
ordinary words, or the description matched decorative terms like
`background`, `texture`, `wood grain` — while containing no important terms
like theorem/code/function — the region was classified `DECORATIVE_IMAGE` and
excluded from Markdown.

The decorative vocabulary was narrow. Photos, ads, avatars, and course logos
could still enter raw Markdown as `<figure>` blocks whenever the VLM produced
a longer description.

### 3.5 Handwriting

The reliable handwriting route depended on the user marking the document
`HANDWRITTEN_NOTES`, or on text density low enough to classify as
`HANDWRITTEN_SCAN`.

Processing:

1. Each page rendered as a full-page PNG; no local crops.
2. One `HANDWRITTEN` region created per page.
3. The VLM called per page, required to transcribe faithfully, preserve
   layout/relationships, and output uncertainty.
4. Layout blocks used `transcription or description`.
5. The Markdown builder used the layout transcription as the page body and
   filtered duplicate visual copies of the same VLM result.
6. Chunks used `PAGE_AWARE`, keeping page boundaries; short adjacent pages
   could merge, oversized pages could split.

Failure policy: full-page VLM retried up to the configured attempts (default
3); timeouts, 429s, and common 5xx errors backed off and retried. Any
required page that ultimately failed failed the whole parse — blank
handwritten pages were never silently produced.

Limitations at the time:

- Local handwritten annotations inside ordinary `TEXT_PDF` documents had no
  dedicated visual detector; regions were labeled `HANDWRITTEN` only when the
  document type was `HANDWRITTEN_NOTES`.
- Local Tesseract OCR was not a success-path fallback for handwriting; the
  route required VLM success.
- Formula structure, reading order, and `[unclear]` usage in transcriptions
  depended on model output quality.

## 4. What Local OCR Actually Did

`visual.py` attempted Tesseract when:

- a page had fewer than 160 native characters; or
- a page had image blocks and image coverage ≥ 12%.

OCR results under 20 characters were dropped; kept results were capped at
4000 characters. They entered the page asset's `visual_summary` and the
no-VLM visual `WorkingBlock.summary`.

However, `build_markdown_page()` skipped `IMAGE`/`MIXED_VISUAL` layout blocks
and rendered visual Markdown only from successful `vlm_results`. So in the
main flow at the time:

- local OCR served asset diagnostics and intermediate metadata;
- for failed or unselected VLM regions on the hybrid route, OCR was **not
  guaranteed to reach the final raw Markdown**;
- on the forced full-page-VLM route, OCR could not substitute for a failed
  VLM.

This was the most commonly misunderstood point in the older documentation.

## 5. Raw Markdown Assembly and Quality Flags

Per-page assembly rules:

1. Skip `BOILERPLATE`.
2. Render non-visual layout blocks first.
3. Ignore VLM results carrying `error_message`.
4. Classify, filter, and render successful visual results.
5. If the page ends up empty, write `<!-- No extractable content on page N. -->`.
6. Compute the quality score and persist warnings.

Common warnings:

- `decorative_visual_filtered`
- `full_page_visual_duplicate_of_text`
- `visual_text_duplicate_of_pdf_text`
- `duplicate_visual_region_filtered`
- `empty_visual_region_filtered`
- `empty_markdown_page`

The quality score started at 1.0 with simple deductions for short pages,
warnings, and filtered regions. It was not an OCR/VLM accuracy metric and
could not by itself prove completeness.

## 6. Configuration and Runtime Prerequisites

Relevant defaults lived in `services/worker/noteflow_worker/config.py`:

| Setting | Default | Purpose |
|---|---:|---|
| `VISION_PROVIDER` | `disabled` | `gemini`, `openai`, or disabled |
| `GEMINI_VISION_MODEL` | `gemini-2.5-flash` | Gemini vision model |
| `OPENAI_VISION_MODEL` | `gpt-4o-mini` | OpenAI vision model |
| `VISION_MAX_REGIONS_PER_DOCUMENT` | 24 | Region cap per document |
| `VISION_REQUEST_TIMEOUT_SECONDS` | 60 | Single-request timeout |
| `VISION_REQUEST_MAX_ATTEMPTS` | 3 | Max attempts |
| `VISION_RETRY_BACKOFF_SECONDS` | 2.0 | Linear backoff base |

With the provider `disabled` or the API key missing:

- the scan/handwriting route necessarily failed, since it required VLM
  success;
- the native-text route still completed, but image content generally never
  reached raw Markdown.

## 7. Conclusions at Audit Time

| Content type | Primary strategy then | Completeness verdict |
|---|---|---|
| Plain text | PyMuPDF block extraction + heuristic structuring | Generally usable; complex layouts and headers/footers risky |
| Text-layer formulas | Heuristic detection + limited glyph repair + `$$` wrapping | Searchable, not reliable LaTeX |
| Image formulas | Region VLM + `latex` field | Conditionally usable; the long-transcription branch could lose LaTeX |
| Important text images | Crop/full-page fallback + VLM transcription + dedupe | Fairly complete for short/medium documents; long documents and region caps could drop content |
| Useless images | Repeated-aHash filter + VLM decorative-term filter | Heuristic only; both false keeps and false drops possible |
| Handwritten PDFs | Forced per-page VLM + page-aware chunks | Clear route, but entirely dependent on VLM success and transcription quality |

## 8. Confirmed Technical Debt and Priorities (as recorded then)

1. **P0: make the OCR fallback actually reach raw Markdown.** OCR
   intermediate results were lost at the Markdown layer for failed/unselected
   regions.
2. **P0: fix the formula-image long-transcription branch.** `FORMULA_IMAGE`
   should keep `latex` first with the transcription attached, instead of
   being cut off by the generic ">12 words" branch.
3. **P1: switch to per-page source classification.** Document-level average
   character density could not handle mixed scanned pages.
4. **P1: record unanalyzed visual regions for long documents.** The 24-region
   cap and the 120-page/8-region policy could silently drop important images.
5. **P1: add local handwriting detection.** The system should not depend
   entirely on the user selecting `HANDWRITTEN_NOTES`.
6. **P2: use bbox positions for header/footer filtering, and perceptual-hash
   distance instead of exact equality for repeated images.**
7. **P2: add per-content-type regression fixtures** covering at least text,
   multi-column, text formulas, formula images, code screenshots, decorative
   images, local handwriting, and full-page handwriting.

All P0/P1 items above were addressed by the Converter V2 rewrite; see
`PDF_CONVERTER_V2_ARCHITECTURE.md`.

## 9. Code Fact Index (at audit time)

| Fact | Location |
|---|---|
| Source-type thresholds | `pdf/parser.py::detect_content_source_type` |
| Routing priority | `pdf/strategies.py::resolve_processing_strategy` |
| The two main pipelines | `pipelines/parse_document.py::ParseDocumentPipeline.run` |
| Page rendering and OCR conditions | `pdf/visual.py::analyze_pdf_visuals`, `should_run_ocr` |
| Region cropping, repetition filtering, long-document selection | `pdf/regions.py` |
| Text block/formula/table formatting | `pdf/layout.py::extract_page_text_blocks`, `format_block_content` |
| Limited math glyph repair | `pdf/math_normalizer.py` |
| Vision structured-output contract and retries | `vision/providers.py`, `pdf/regions.py::analyze_regions_with_vlm` |
| Markdown visual classification/filter/dedupe | `pdf/markdown.py` |
| Raw Markdown to chunks | `pdf/layout.py::build_markdown_chunks` |
