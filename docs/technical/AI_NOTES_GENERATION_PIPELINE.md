# AI Notes Generation Pipeline

This document defines how NoteFlow should generate AI study notes from already parsed document Markdown and chunks.

The goal is:

```text
PDF -> Markdown -> Chunks -> AI Notes Markdown
```

The notes generator must not read the raw PDF directly. It should use the cleaned Markdown, chunk metadata, page references, visual descriptions, formulas, code blocks, and document type strategy already produced by the PDF pipeline.

## 1. Product Goal

AI notes should help a student study the uploaded document.

They should be:

1. Grounded in source pages and chunks.
2. Written in clean Markdown.
3. Easy to edit later in a Markdown/Tiptap editor.
4. Good for semantic search and RAG.
5. Different for lecture slides, course notes, handwritten notes, assignments, exams, and papers.
6. Conservative about unsupported claims.

The generated notes are not a generic summary. They are source-grounded study material.

## 2. Pipeline Overview

```text
User clicks Generate Notes
  -> Backend creates GENERATE_NOTES task
  -> Backend enqueues Redis task
  -> Worker loads document metadata
  -> Worker loads Markdown document and chunks
  -> Worker builds note generation plan
  -> Worker generates section notes with LLM
  -> Worker validates source references
  -> Worker stores AI notes Markdown and structured sections
  -> Backend returns notes to frontend
```

The parse pipeline remains separate from the notes pipeline:

```text
PARSE_DOCUMENT
  -> document_markdown_documents
  -> document_chunks

GENERATE_NOTES
  -> document_ai_notes
  -> document_ai_note_sections
```

## 3. Inputs

Required inputs:

| Input | Source | Purpose |
|---|---|---|
| Document metadata | `documents` | Title, type, source type, page count, status. |
| Full Markdown | `document_markdown_documents` | Global structure and page markers. |
| Page Markdown | `document_markdown_pages` | Page-level source reconstruction. |
| Chunks | `document_chunks` | Grounded semantic units. |
| Layout metadata | `document_layout_blocks` | Optional source inspection and block type analysis. |
| Visual metadata | `document_visual_regions`, `document_vlm_results` | Optional image/code/diagram descriptions. |

The minimum viable implementation can start with:

```text
documents
document_markdown_documents
document_chunks
```

## 4. Outputs

Primary output:

```text
document_ai_notes.markdown
```

Secondary structured output:

```text
document_ai_note_sections
```

Each note section should preserve:

1. Heading.
2. Markdown content.
3. Source chunk IDs.
4. Page range.
5. Confidence or quality flags.
6. Section type.

Example note Markdown:

```markdown
# STAT230CourseNote - AI Notes

## Chapter 3: Discrete Probability Distributions

### Key Ideas
- A discrete random variable assigns probabilities to countable outcomes.
- A probability mass function must be non-negative and sum to 1.

### Definitions
- **Probability mass function (pmf):** A function \(f(x)\) that gives \(P(X=x)\).

### Important Formulas
$$
\sum_x f(x) = 1
$$

### Worked Examples
#### Geometric Distribution
The geometric distribution models the number of failures before the first success.

### Common Pitfalls
- Do not confuse \(P(X=x)\) with \(P(X\le x)\).

### Sources
- Pages 100-109
- Chunks 126-128
```

## 5. Proposed Database Schema

### document_ai_notes

One generated note document per uploaded document and generation version.

```sql
CREATE TABLE document_ai_notes (
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
);
```

Recommended `status` values:

```text
GENERATING
READY
FAILED
SUPERSEDED
```

### document_ai_note_sections

One row per generated section.

```sql
CREATE TABLE document_ai_note_sections (
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
);
```

Recommended `section_type` values:

```text
OVERVIEW
KEY_IDEAS
DEFINITION
THEOREM
FORMULA
EXAMPLE
PROOF
CODE_EXPLANATION
DIAGRAM_EXPLANATION
PITFALL
QUESTION_SUMMARY
PAPER_SECTION
REVIEW_CHECKLIST
SOURCE_INDEX
```

## 6. Generation Plan

The worker should not send an entire long document to the LLM in one request.

Instead:

```text
chunks -> section groups -> section note generation -> document synthesis
```

### 6.1 Group Chunks

Group chunks by:

1. `document_type`.
2. `chunkStrategy`.
3. Heading path.
4. Page range.
5. Chunk type.
6. Token budget.

Recommended group size:

```text
target: 1800-3000 tokens
max:    5000 tokens
```

This is larger than retrieval chunks because note generation needs broader context.

Coverage rule:

1. The notes pipeline must cover every available source chunk for the selected document.
2. It must not silently stop after a fixed number of source groups.
3. Long documents should produce more source groups, not truncated notes.
4. If a generation budget or provider limit prevents full coverage, the task should fail or explicitly report incomplete coverage instead of returning a partial note as `READY`.

### 6.2 Generate Section Notes

For each group:

1. Build source bundle.
2. Call LLM with a document-type-specific prompt.
3. Request Markdown plus structured metadata.
4. Store provisional section result.
5. Validate that cited source chunks exist.

### 6.3 Synthesize Final Notes

After all section notes are generated:

1. Merge sections in page order.
2. Build top-level table of contents.
3. Add source index.
4. Remove repeated boilerplate.
5. Store one final Markdown document in `document_ai_notes.markdown`.

The UI should present this final merged Markdown as the primary note. `document_ai_note_sections` are internal structured records for traceability, regeneration, source linking, and future editor features; they should not replace the single combined note display.

## 7. Document Type Strategies

### COURSE_NOTES

Best output structure:

```text
Overview
Chapter/topic sections
Definitions
Theorems/propositions
Important formulas
Worked examples
Common pitfalls
Review checklist
Sources
```

Rules:

1. Preserve formulas in LaTeX where possible.
2. Keep theorem/proof relationships.
3. Explain notation before using it.
4. Do not over-compress worked examples.
5. Prefer chapter/topic headings over page headings.

### LECTURE_SLIDES

Best output structure:

```text
Lecture overview
Slide-topic summaries
Key takeaways
Code or diagram explanations
Instructor emphasis
Review checklist
Sources
```

Rules:

1. Merge short slides into coherent topics.
2. Preserve code blocks.
3. Explain diagrams only if visual text/description exists.
4. Avoid making a slide deck sound like a textbook chapter.

### HANDWRITTEN_NOTES

Best output structure:

```text
Page/topic summary
Transcribed formulas
Key reasoning steps
Unclear handwriting notes
Review checklist
Sources
```

Rules:

1. Be explicit about uncertainty.
2. Do not invent missing handwriting.
3. Keep source page references tight.
4. Preserve the order of the handwritten derivation.

### RESEARCH_PAPER

Best output structure:

```text
Paper overview
Research question
Contributions
Methods
Results
Limitations
Important figures/tables
Reading checklist
Sources
```

Rules:

1. Keep claims tied to sections.
2. Do not convert uncertain results into stronger claims.
3. Separate author claims from generated interpretation.
4. References section should be summarized only if useful.

### ASSIGNMENT

Best output structure:

```text
Assignment overview
Question-by-question notes
Required concepts
Starter code explanation
Constraints
Common mistakes
Checklist
Sources
```

Rules:

1. Do not produce full solutions unless the user explicitly asks.
2. Explain what each question is testing.
3. Preserve constraints, due-date-like instructions, and grading notes.
4. Keep code snippets attached to the relevant question.

### PAST_EXAM

Best output structure:

```text
Exam overview
Question topics
Formula sheet summary
Common exam patterns
Time-management notes
Review checklist
Sources
```

Rules:

1. Preserve marks and subparts.
2. Do not invent official solutions.
3. Summarize likely tested concepts.
4. Keep formula sheet separate when present.

### OTHER

Use conservative generic notes:

```text
Overview
Main sections
Key facts
Examples
Open questions
Sources
```

## 8. Markdown Organization Standard

Generated notes must be organized, predictable, and editor-friendly.

The output should be valid Markdown with a stable heading hierarchy:

```text
# Document Title - AI Notes
## Major topic / chapter / lecture section
### Standard note section
#### Optional subsection
```

Do not skip from `##` to `####`. Do not use heading levels as decoration.

## 8.1 Required Top-Level Order

Every generated note should use this order unless a document type has a strong reason to omit a section:

```markdown
# {Document Title} - AI Notes

## Overview

## How To Study This Document

## Main Notes

## Important Definitions

## Important Formulas

## Worked Examples

## Common Pitfalls

## Review Checklist

## Source Index
```

For short slide decks or handwritten notes, some sections can be merged, but the final Markdown should still be easy to scan.

## 8.2 Section Template

Each major note section should follow this structure:

```markdown
## {Topic Heading}

### Key Ideas
- ...

### Details
...

### Formulas
$$
...
$$

### Example
...

### Why This Matters
...

### Sources
- Pages {page_start}-{page_end}; chunks `{chunk_index}`, `{chunk_index}`
```

Rules:

1. Use bullets for lists of facts.
2. Use paragraphs for explanations.
3. Use numbered lists only for ordered procedures or proof steps.
4. Keep source citations at the end of each section.
5. Avoid giant paragraphs over 8 lines.
6. Avoid a flat list of disconnected bullets for the whole note.

## 8.3 Formula Formatting

Inline formulas:

```markdown
The expected value is \(E[X]\).
```

Block formulas:

```markdown
$$
Var(X) = E[X^2] - (E[X])^2
$$
```

Piecewise formulas:

```markdown
$$
f(x) =
\begin{cases}
0, & x < 0 \\
1 - (1-p)^{\lfloor x \rfloor + 1}, & x \ge 0
\end{cases}
$$
```

Rules:

1. Preserve formulas from source chunks.
2. Do not invent missing formulas.
3. If extracted math looks corrupted, include a warning in the section metadata.
4. Prefer readable LaTeX-ish formatting over raw PDF extraction artifacts.

## 8.4 Code Formatting

Code must use fenced blocks with a language label when known:

````markdown
```python
def example():
    return 1
```
````

Rules:

1. Preserve indentation.
2. Keep code with the explanation that describes it.
3. Do not summarize code into prose when exact code matters.
4. If language is unknown, use `text`.

## 8.5 Tables

Use Markdown tables only when the table is small and readable:

```markdown
| Concept | Meaning |
|---|---|
| pmf | Probability mass function |
| cdf | Cumulative distribution function |
```

For large or messy tables, use bullets with labels instead of broken tables.

## 8.6 Source Citation Format

Every major section must include source citations:

```markdown
### Sources
- Pages 104-109; chunks `126`, `127`, `128`
```

If a section is synthesized from many chunks:

```markdown
### Sources
- Primary: pages 104-109; chunks `126`, `127`, `128`
- Supporting: pages 112-114; chunks `131`, `132`
```

Rules:

1. Cite chunk indexes for human readability.
2. Store source chunk UUIDs in `document_ai_note_sections.source_chunk_ids_json`.
3. Do not show UUIDs in the main Markdown unless debugging.
4. If no citation can be validated, the section should be marked with `missing_source_citation`.

## 8.7 Style Constraints

The notes should:

1. Be concise but not skeletal.
2. Explain relationships between concepts.
3. Preserve technical precision.
4. Use consistent heading names.
5. Use source-grounded wording.
6. Avoid marketing tone.
7. Avoid unsupported outside knowledge.
8. Avoid saying "the document discusses" repeatedly.

The notes should not:

1. Dump raw chunks.
2. Produce only a generic summary.
3. Hide uncertainty.
4. Remove formulas, code, or examples.
5. Mix unrelated pages into one section without explanation.

## 9. Prompt Contract

The notes pipeline is AI-first: the note body must be generated by an AI provider from source chunks.
The worker may perform mechanical formatting, source attachment, validation, retry, and persistence, but it must not create substitute study-note content when the AI provider is unavailable.

The LLM prompt requests JSON. The model should not be asked to invent or return database UUIDs; those are attached by the worker from the known source group.

Recommended structured response:

```json
{
  "heading": "Geometric Distribution",
  "sectionType": "FORMULA",
  "markdown": "## Geometric Distribution\n...",
  "confidence": 0.86,
  "warnings": []
}
```

The prompt must include:

1. Document title.
2. Document type.
3. Source page range.
4. Chunk IDs and human chunk indexes.
5. Chunk text.
6. Required output format.
7. Instruction not to use outside knowledge unless explicitly marked.

Core instruction:

```text
Use only the provided source chunks. If a concept is unclear or missing, say so.
Preserve formulas, code, definitions, examples, and theorem/proof structure.
```

Current implementation detail:

1. AI generates the section heading, section type, Markdown note body, confidence, and warnings.
2. Worker strips any malformed model-generated `### Sources` block.
3. Worker appends a verified `### Sources` block using the source group's known page range and chunk indexes.
4. Worker stores UUID source links in `document_ai_note_sections.source_chunk_ids_json`.
5. If the worker has to attach or repair citations, it adds `source_citations_attached_by_system` to section warnings.

This keeps content generation AI-first while making source traceability deterministic.

## 10. Source Bundle Format

The worker should send chunks in a stable format:

```markdown
<source_chunk id="chunk_uuid" index="127" pages="108-109" type="FORMULA">
Therefore, the full cdf of X can be expressed as ...
</source_chunk>
```

Why:

1. The LLM sees the source boundary and human chunk indexes.
2. The worker can bind generated sections back to verified chunk UUIDs.
3. The final frontend can show source previews without trusting model-generated IDs.

## 11. Validation

After each LLM response:

1. Parse response.
2. Check Markdown is non-empty.
3. Normalize or attach the `### Sources` block from the known source group.
4. Check the final Markdown cites at least one human chunk index from that group.
5. Check no raw `<source_chunk>` prompt tags leaked into output.
6. Check output is not mostly copied source text.

Quality warnings:

```text
missing_source_citation
source_citations_attached_by_system
empty_markdown
low_confidence
too_much_verbatim_source
formula_may_be_corrupted
unclear_handwriting
model_error
```

## 12. Failure And Retry Policy

A notes generation task should be resumable.

Rules:

1. Store section results incrementally.
2. Retry transient model errors.
3. Do not delete previous ready notes until a new version is complete.
4. If final synthesis fails, keep completed section drafts with status `FAILED`.
5. A failed note generation task should not change the parsed document status.

Recommended retry settings:

```text
max attempts: 3
backoff: 2s, 4s, 8s
retryable: timeout, 429, 5xx, temporary network failures
```

## 13. API Design

### Generate Notes

```text
POST /documents/{documentId}/notes
```

Response:

```json
{
  "noteId": "uuid",
  "taskId": "uuid",
  "status": "GENERATING"
}
```

### Get Latest Notes

```text
GET /documents/{documentId}/notes
```

Response:

```json
{
  "id": "uuid",
  "documentId": "uuid",
  "noteVersion": 1,
  "status": "READY",
  "title": "STAT230CourseNote - AI Notes",
  "markdown": "...",
  "summary": "...",
  "qualityReportJson": "{}",
  "createdAt": "..."
}
```

### Get Note Sections

```text
GET /notes/{noteId}/sections
```

### Update Notes

```text
PUT /notes/{noteId}
```

Used by the editor after user modifications.

### Export Notes

```text
POST /notes/{noteId}/export/markdown
POST /notes/{noteId}/export/pdf
```

## 14. Frontend View

The frontend should have a document detail area with tabs:

```text
Summary
Markdown
Chunks
AI Notes
Visual Regions
VLM Results
```

AI Notes tab:

1. Generate notes button.
2. Task progress.
3. Rendered Markdown preview.
4. Source citations beside sections.
5. Edit button.
6. Export button.

Source citation interaction:

```text
Click source chip -> show chunk content + page image/page number
```

## 15. Implementation Phases

### Phase 1: Notes Storage And API

1. Add `document_ai_notes`.
2. Add `document_ai_note_sections`.
3. Add Spring repositories/controllers.
4. Add `POST /documents/{id}/notes`.
5. Add `GET /documents/{id}/notes`.

### Phase 2: Worker Pipeline

1. Add `GenerateNotesPipeline`.
2. Load document/chunks/Markdown.
3. Build source groups.
4. Generate section notes.
5. Store final Markdown.

### Phase 3: Frontend Notes Tab

1. Add generate notes button.
2. Show task state.
3. Render notes Markdown.
4. Show source chunk references.

### Phase 4: Quality Improvements

1. Better document-type prompts.
2. Citation validation.
3. Section-by-section retry.
4. Notes regeneration with versioning.
5. Editor integration.

## 16. MVP Acceptance Criteria

For a parsed document:

1. User can click `Generate Notes`.
2. Backend creates a `GENERATE_NOTES` task.
3. Worker generates Markdown notes from chunks.
4. Notes include source page/chunk references.
5. Frontend displays the generated notes.
6. Existing parsed Markdown/chunks remain unchanged.
7. Failed notes generation does not mark the document parse as failed.

## 17. Open Decisions

1. Which model should be used first: Gemini, OpenAI, or provider abstraction.
2. Whether notes should be generated automatically after parsing or only on user click.
3. Whether user-edited notes overwrite AI notes or create a separate version.
4. Whether notes should be embedded for semantic search immediately.
5. How strict the verbatim-source limit should be for generated notes.

Recommended initial decisions:

1. Use provider abstraction and support Gemini/OpenAI.
2. Trigger notes manually first.
3. Store AI notes and user-edited notes as versions.
4. Embed notes after the first notes API works.
5. Keep citations mandatory for every generated section.
