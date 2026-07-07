# NoteFlow Document Processing Pipeline Architecture

Status: final-state description of the current implementation (2026-07-06).
This document only describes what the system is now: its organization, inputs
and outputs, how each class of input is handled, and how the pipelines
coordinate. It contains no historical evolution or intermediate designs.

Code: `services/worker/noteflow_worker/`.

---

## 1. Architecture

### 1.1 System level

```text
                         ┌─────────────────────────────┐
  API (upload/trigger) ─▶│ PostgreSQL                   │◀─── API (query/export)
        │                │  documents / tasks /         │
        │ push           │  pipeline artifact tables    │
        ▼                └─────────────────────────────┘
┌──────────────────────────────┐       ▲ read/write
│ Redis queue:document-analysis│       │
│  priority:0 interactive (ASK)│       │
│  priority:1 user-visible     │       │
│             (PARSE/NOTES)    │       │
│  priority:2 background       │       │
│             (EMBEDDINGS)     │       │
└──────────────────────────────┘       │
        │ weighted round-robin pop     │
        ▼                              │
┌──────────────────────────────────────┴───────────────────────────┐
│ Worker process (main.py)                                          │
│  ThreadPoolExecutor: document-level concurrency                   │
│    ≤ WORKER_MAX_CONCURRENT_TASKS (3)                              │
│  background tasks occupy ≤ WORKER_MAX_BACKGROUND_TASKS (1) slots  │
│                                                                   │
│  PARSE_DOCUMENT              ─▶ ParseDocumentPipeline             │
│  GENERATE_NOTES              ─▶ GenerateNotesPipeline             │
│  GENERATE_EMBEDDINGS         ─▶ GenerateEmbeddingsPipeline        │
│  MAINTAIN_CONVERSATION_MEMORY─▶ MaintainConversationMemoryPipeline│
│                                                                   │
│  Process-wide semaphore pools (shared across concurrent docs):    │
│   pdf_render │ cpu_ocr / gpu_ocr │ vlm                            │
└───────────────────────────────────────────────────────────────────┘
        │ external calls
        ▼
┌───────────────────────────────────────────────┐
│ Provider layer                                 │
│  Vision: Gemini / OpenAI / MCP                 │
│    (RouterVisionProvider: multi-provider,      │
│     multi-key rotation + failure cooldown)     │
│  Notes:  Gemini / OpenAI (structured JSON)     │
│  Embedding: Gemini / OpenAI / local            │
│  Local OCR: EasyOCR (MPS) / PaddleOCR (CUDA) / │
│             Tesseract (CPU)                    │
└───────────────────────────────────────────────┘
```

### 1.2 Inside the parse pipeline (ParseDocumentPipeline)

Each stage is annotated with the progress value written to `tasks`.

```text
Input: storage/uploads/<file>.pdf + documents row
       (including the user-selected document_type)
  │
  │ 10%  PARSING_PDF            resource-pool planning (CPU/IO/GPU/VLM
  │                             worker derivation)
  ▼
  │ 25%  EXTRACTING_TEXT        per-page pypdf text profiling
  │                             → content_source_type + confidence +
  │                               page distribution
  ▼
  │ 38%  ANALYZING_VISUAL_CONTENT
  │        MuPDF render pool: 144-dpi PNG per page →
  │          storage/rendered/<docId>/
  │        OCR pool: local OCR only for weak-text pages or pages with
  │          image coverage ≥ 12%
  │        Per-page router: NATIVE_TEXT | HYBRID | FULL_PAGE_VLM
  │        → document_parse_manifests (per-page route + reasons, auditable)
  ▼
  │ 50%  CROPPING_VISUAL_REGIONS
  │        Region discovery: embedded-image crops / native-formula layout
  │          recovery crops / full-page regions (reference the rendered
  │          PNG directly, no file copy)
  │        Repeated-aHash decorative filtering + VLM budget selection
  │        → document_visual_regions, storage/regions/<docId>/
  ▼
  │ 62%  VLM_ANALYSIS
  │        Single thread pool, concurrency = vlm pool cap; fingerprint
  │          hits reuse historical results with zero API calls
  │        Every region upserted the moment it completes (checkpointing)
  │        Handwritten/full-page transcriptions cross-checked against the
  │          local OCR baseline for completeness
  │        → document_vlm_results
  ▼
  │ 76%  LAYOUT_CHUNKING
  │        Native text-block classification (paragraph/heading/list/code/
  │          formula/table)
  │        VLM formulas replace covered native formula blocks / two-column
  │          reading order / header-footer boilerplate marking
  │        → document_layout_blocks
  │        Markdown assembly (page anchors <!-- page:N -->, quality gate)
  │        → document_markdown_pages, document_markdown_documents
  ▼
  │ 88%  CHUNKING
  │        Chunk strategy selected by document_type, cut from the raw
  │          Markdown
  │        → document_chunks
  ▼
  │ 100% COMPLETED
  │        document_parse_results summary + orphaned intermediate-file
  │          cleanup
```

### 1.3 AI Notes and Embeddings pipelines

```text
GenerateNotesPipeline
  document_chunks ─▶ grouped into source groups by token budget
                     (target 3200 / cap 4500)
                 ─▶ thread pool (≤ NOTES_MAX_CONCURRENT_REQUESTS=3),
                     one LLM call per group → several note sections
                     (structured JSON)
                 ─▶ each section persisted on completion (resumable);
                     citation validation / tag-leak checks / Sources
                     subsection enforcement
                 ─▶ all groups succeed: assemble the full note →
                     document_ai_notes
                     some groups fail: note marked "paused" with the failed
                     groups recorded; re-trigger skips completed groups

GenerateEmbeddingsPipeline
  document_chunks + document_ai_note_sections
                 ─▶ content-hash comparison, unchanged entries skipped
                 ─▶ batched embedding (EMBEDDING_BATCH_SIZE=16)
                 ─▶ document_embeddings
```

---

## 2. Inputs and Outputs

### 2.1 Pipeline inputs

| Input | Source | Notes |
|---|---|---|
| PDF file | `storage/uploads/` | Multimodal: native text, scans, handwriting, formulas, code, tables, figures, multi-column layouts |
| `document_type` | Chosen by the user at upload | `HANDWRITTEN_NOTES / LECTURE_SLIDES / COURSE_NOTES / RESEARCH_PAPER / ASSIGNMENT / PAST_EXAM / OTHER` — the authoritative intent signal for routing and chunking |
| TaskPayload | Redis queue | `task_id / document_id / user_id / task_type` (+ `conversation_id` for memory tasks) |

### 2.2 File artifacts

| Path | Content | Lifecycle |
|---|---|---|
| `storage/rendered/<docId>/page-NNN.png` | 144-dpi render of every page | Kept; full-page regions reference it directly (no duplicate copy) |
| `storage/regions/<docId>/page-NNN-region-NN.png` | Cropped region images | After a successful parse, unreferenced orphan files are deleted |

### 2.3 Database artifacts (by stage)

| Table | Stage | Content |
|---|---|---|
| `document_parse_manifests` | 38% | Per-page routing decision + reasons, resource-pool plan, VLM selection/skip audit |
| `document_page_assets` | 38% | Per-page render metadata (size, image count, coverage, OCR summary) |
| `document_visual_regions` | 50% | Region type, bbox, image file, aHash, OCR baseline length |
| `document_vlm_results` | 62% | Structured ten-field result + input fingerprint + attempt count + error |
| `document_layout_blocks` | 76% | Classified text blocks (bbox, heading path, noise assessment) |
| `document_markdown_pages` / `document_markdown_documents` | 76% | Page/document Markdown + structure index + quality report |
| `document_chunks` | 88% | RAG chunks (content, page range, type, strategy context, asset references) |
| `document_parse_results` | 100% | Parse summary (source type, confidence, page distribution, preview) |
| `document_ai_notes` / `document_ai_note_sections` | notes task | AI note document / sections (source-chunk citations, quality report) |
| `document_embeddings` | embeddings task | Vectors + content hash (incremental-update basis) |

### 2.4 Final output contracts

- **Raw Markdown**: page-anchored (`<!-- page:N -->`); formulas as `$$…$$`
  display blocks, code as language-tagged fenced blocks, tables as Markdown
  tables, figures as structured `<figure>` blocks; accompanied by a quality
  report (native-token coverage, formula/code fence balance, empty-page
  count, unprocessed visual blocks, quality-gate pass/fail).
- **Chunks**: each chunk carries content, page range, chunk type, chunking
  strategy, `containsFormula/Code/Table/Image` flags, linked page-asset ids,
  and bbox references — retrieval results can be traced back to the exact
  location in the original PDF.
- **AI Notes**: sectioned notes; each section has a type
  (definition/theorem/example/…), confidence, warnings, and source-chunk
  citations; the document ends with a Source Index.

---

## 3. Case Matrix

### 3.1 Page-level routing

| Case | Evidence | Route | Handling |
|---|---|---|---|
| Reliable native-text page | Sufficient text layer with a high quality score, no significant visuals | `NATIVE_TEXT` | Pure PyMuPDF layout parsing, zero VLM calls |
| Text + images/dense vector drawings | Usable text layer plus `image_count>0` or drawings ≥ 8 or image coverage ≥ 4% | `HYBRID` | Native text is primary; local region crops go to the VLM as supplements |
| Nearly empty text layer (scanned page) | Native text < 24 chars with visual/OCR evidence | `FULL_PAGE_VLM` | Whole page to the VLM, garbage text layer suppressed; the VLM result is required (failure fails the task) |
| Weak text layer but OCR reads much more | Text < 80 chars or quality < 0.38, and OCR chars > max(40, 2× native) | `FULL_PAGE_VLM` | Same as above — never depend on a corrupted text layer |
| User declares handwritten notes | `document_type=HANDWRITTEN_NOTES` | All pages `FULL_PAGE_VLM` | Region type `HANDWRITTEN`; VLM required, silent degradation is not allowed |

Every page decision and its reasons are written to the manifest and can be
audited via `GET /documents/{id}/parse-manifest`.

### 3.2 Content-level handling

| Case | Recognition | Handling |
|---|---|---|
| Multi-line / 2-D native formulas (fractions, limits, cases) | Semantic classification + layout evidence (≥ 3 visual lines, not prose-dominant) | Cropped as `FORMULA_IMAGE` for the VLM; successful LaTeX **replaces** the covered native formula blocks (bbox overlap ≥ 45%); independent formulas separated by `---FORMULA---` render as separate `$$` blocks |
| Single-line / inline math | Line-level formula heuristics | Fast native path; Unicode mathematical alphanumerics (U+1D400–1D7FF) transliterated to ASCII/Greek (`𝑦2 = 𝑓(𝑥2)` → `y2 = f(x2)`), combining negation slash normalized (`̸=` → `≠`); vertically fragmented formulas linearized to one line pending VLM recovery |
| cases/bracket glyph fragments | Private-use glyph-run detection | `\begin{cases}` environment rebuilt with balanced row separators and `\end{cases}` |
| Code (native or screenshot) | Line-level code heuristics / SQL structure / VLM `content_kind=code` | Language detection + fenced code block; indentation preserved, identifiers never translated |
| Tables | Column-shape consistency / `\|` separators / VLM `content_kind=table` | Converted to Markdown tables; row/column relationships kept explicit in transcriptions |
| Two-column body text | Left/right bbox clustering + vertical overlap ≥ 35% | Reading order rebuilt: wide separator blocks → left column → right column |
| Handwritten page transcription | `HANDWRITTEN` region VLM | Structured transcription (arrows/derivation order/strike-through semantics), `[unclear]` markers; **completeness cross-check**: if local OCR read ≥ 200 chars but the VLM produced < 45% of that, the result is flagged `transcription_may_be_incomplete`, surfaced as the page warning `handwritten_transcription_may_be_incomplete`, and counted in the quality report |
| Decorative images / repeated logos / backgrounds | aHash repeated on ≥ max(3, 15% of pages), non-critical type, coverage < 8% | Dropped (regions that reuse the page render delete only the DB row, never the render file); VLM-declared `decorative` filtered too |
| Headers/footers/watermarks | Edge position + cross-page exact/numeric-family repetition, document ≥ 8 pages | Multi-evidence scoring: ≥ 0.84 exclude, ≥ 0.62 annotate only; formula/code/table/high-symbol-density content is **hard-protected** and never deleted for repetition |
| VLM transcription duplicates body text | Token overlap ≥ 70% or sequence similarity ≥ 0.82 | Deduplicated, body text wins; full-page dedupe **preserves structured LaTeX/code/figure explanations** |
| Empty page / nothing extractable | No blocks produced | Placeholder comment + `empty_markdown_page` warning, counted by the quality gate |

### 3.3 Failures and fallbacks

| Failure case | Response | Fallback chain |
|---|---|---|
| Transient VLM failure (timeout/429/5xx/connection reset) | Region-level retry ≤ 3 with exponential backoff + jitter, 30s cap | During retries the router fails over across provider/key instances; 429/5xx trigger exponential cooldown, 401/403 a 300s cooldown |
| VLM structured-output validation failure (missing fields / invalid JSON) | Treated as stochastic model behavior — **also retried** | Same as above |
| Required region (handwritten/full-page) ultimately fails | Parse task fails with the error persisted | Stale-task recovery re-enqueues on worker restart (≤ PARSE_MAX_TASK_RETRIES=3); already-successful regions are reused via fingerprints, never re-billed |
| Optional region ultimately fails | Task continues; the error stays in `document_vlm_results` | Page-render OCR text enters the Markdown as low-confidence `<figure data-type="ocr-fallback">` |
| Local OCR backend unavailable | Automatic downgrade | EasyOCR (MPS) → PaddleOCR (CUDA) → Tesseract (CPU) → disabled (VLM/native text carry the load) |
| A notes source group fails | That group is marked failed; the rest complete; the note is "paused" with the first error attached | Re-trigger skips completed groups and resumes from the failure; HTTP and validation errors were already retried ≤ 3× inside the group |
| Worker crash / stuck task | Startup scan for PROCESSING tasks stale beyond 10 min (parse/notes) | Re-enqueued; all parse artifacts use replace semantics, so re-runs are safe |
| Re-parsing the same document | Region input fingerprint (image bytes + type + bbox + prompt version) hit | Historical VLM results reused with zero API calls |
| Embedding content unchanged | Content hash matches the stored vector | Skipped, no repeat call |

### 3.4 Chunking strategies (by document_type)

| document_type / source | Strategy | Token budget (target/max/min-merge) | Boundary rules |
|---|---|---|---|
| HANDWRITTEN_NOTES, scans | `PAGE_AWARE` | 650 / 1000 / 60 | Merge by page; a new-topic page (theorem/example headings) forces a split; oversized pages split internally |
| LECTURE_SLIDES | `SLIDE_AWARE` | 700 / 1100 / 80 | Page unit + heading change = new slide topic |
| COURSE_NOTES | `TOPIC_AWARE` | 650 / 1000 / 80 | Definition/Theorem/Example academic-unit openings force splits |
| RESEARCH_PAPER | `PAPER_SECTION_AWARE` | 800 / 1200 / 100 | Abstract/Method/Results section headings force splits |
| ASSIGNMENT, PAST_EXAM | `QUESTION_AWARE` | 750 / 1200 / 80 | Question/Problem/numbered stems force splits |
| Other | `MIXED_FALLBACK` | 650 / 1000 / 80 | Heading changes + token budget |

Universal rules: tables, large formula/code blocks (≥ 120 tokens), and
standalone visual blocks become **their own chunks** and are never split;
adjacent paragraph-type chunks keep a 90-word semantic overlap (code/table/
formula blocks never leak into the overlap); heading-only fragments are never
emitted as chunks.

---

## 4. Pipeline Coordination

### 4.1 Task scheduling

- Three physical Redis lists layered by priority (interactive 0 /
  user-visible 1 / background 2).
- The worker main loop rotates through the fixed weighted sequence
  `0,1,0,1,0,2,1,2`: high-priority work gets most of the bandwidth while
  background tasks have a guaranteed service window and never starve.
- Document-level concurrency ≤ 3; background tasks occupy at most 1 slot, so
  with total concurrency > 1 at least one slot is always reserved for
  user-visible work.

### 4.2 Resource pools and concurrency

| Pool | Constrains | Cap source | Enforcement |
|---|---|---|---|
| Document | Concurrently processed tasks | `WORKER_MAX_CONCURRENT_TASKS=3` | Main-loop ThreadPoolExecutor |
| MuPDF render | Page rendering | `min(2, cores/4)` (measured: > 2 workers gains nothing) | Process-wide semaphore `pdf_render` |
| OCR | Local recognition | GPU: `floor((freeVRAM − 1536 MiB) / 2048 MiB)` capped at 4; CPU follows the render pool | Process-wide semaphores `cpu_ocr`/`gpu_ocr`; GPU model instances shared per process |
| VLM | Remote vision requests | `VISION_CONCURRENT_REQUESTS=4` | One thread pool over all pending regions + process-wide semaphore `vlm` |
| Notes LLM | Note-generation requests | `NOTES_MAX_CONCURRENT_REQUESTS=3` | Per-task thread pool |

Key point: the semaphores are **process-wide** — three concurrent documents
share one VLM/OCR quota rather than opening N each; the resource-pool plan
and its derivation rationale are stored in the parse manifest.

### 4.3 Checkpointing and idempotency

- **VLM regions**: upserted the moment each completes; re-runs reuse results
  by input fingerprint; the task tail canonicalizes the full set.
- **Notes**: every section is persisted on generation; group completion is
  derived from section metadata; re-triggering skips completed groups.
- **Embeddings**: content-hash incremental; only changed entries are
  embedded.
- All parse artifact tables use replace semantics — re-running any stage
  never produces duplicates.

### 4.4 Quality gate and observability

- Markdown quality report: native-token coverage (< 0.82 flags), `$$`/```
  fence balance, cross-page repeated-line ratio (> 0.18 flags), empty-page
  count, failed/unprocessed visual region counts, handwritten-transcription
  completeness warnings.
- A failed quality gate does not block output; `qualityGateIssues` and
  per-page warnings are all persisted for auditing and re-run decisions.
- Per-page routing reasons, the VLM skip list, and resource-pool derivations
  live in the manifest — nothing needs to be reverse-engineered from the
  final Markdown.

### 4.5 Storage governance

- Full-page regions reference the page-render PNG directly instead of copying
  it (roughly halving parse-artifact volume for handwritten documents);
  reference-type regions never delete the underlying render through dedupe
  logic.
- After a successful parse, orphaned files under `rendered/` and `regions/`
  not referenced by any page asset or region are cleaned up
  (`PDF_CLEANUP_INTERMEDIATE_FILES=true`).

---

## 5. Key Configuration Reference

```text
# Scheduling
WORKER_MAX_CONCURRENT_TASKS=3      WORKER_MAX_BACKGROUND_TASKS=1
PARSE_STALE_TASK_AFTER_MINUTES=10  PARSE_MAX_TASK_RETRIES=3
NOTES_STALE_TASK_AFTER_MINUTES=10

# Resource pools
PDF_CPU_WORKERS=0 (auto)  PDF_IO_WORKERS=0 (auto)  PDF_GPU_WORKERS=0 (auto)
PDF_GPU_MEMORY_PER_TASK_MIB=2048  PDF_GPU_MEMORY_RESERVE_MIB=1536  PDF_GPU_WORKER_CAP=4
PDF_OCR_BACKEND=auto

# VLM
VISION_PROVIDER=gemini|openai|mcp|router   VISION_PROVIDER_ORDER=gemini,openai,mcp
VISION_CONCURRENT_REQUESTS=4
VISION_MAX_REGIONS_PER_DOCUMENT=96  VISION_LONG_DOCUMENT_MAX_REGIONS=64
VISION_FORMULA_RECOVERY_MAX_REGIONS=320
VISION_REQUEST_MAX_ATTEMPTS=3  VISION_RETRY_BACKOFF_SECONDS=2  VISION_RETRY_MAX_BACKOFF_SECONDS=30
GEMINI_API_KEYS / OPENAI_API_KEYS / MCP_VISION_API_KEYS  (comma/newline separated)

# Notes / Embeddings
NOTES_MAX_CONCURRENT_REQUESTS=3  NOTES_REQUEST_MAX_ATTEMPTS=3
NOTES_GROUP_TARGET_TOKENS=3200   NOTES_GROUP_MAX_TOKENS=4500
EMBEDDING_BATCH_SIZE=16          EMBEDDING_MAX_CONCURRENT_REQUESTS=5
```

Benchmark the deployment host before overriding pool sizes:

```bash
cd services/worker
PYTHONPATH=. .venv/bin/python ../../tests/benchmarks/benchmark_pdf_pools.py --pages 48 --workers 1,2,4,8
```
