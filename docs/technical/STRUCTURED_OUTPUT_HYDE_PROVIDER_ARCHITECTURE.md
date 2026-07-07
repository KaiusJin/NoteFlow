# Structured Output, HyDE, And AI Provider Architecture

This document records the current NoteFlow decisions for structured model
responses, backend validation, HyDE query expansion, API-provider portability,
and MCP.

## 1. Audit Result

### AI Notes

AI note generation requests JSON containing:

```text
sections[]
  heading
  sectionType
  markdown
  confidence
  warnings[]
```

Gemini uses JSON Schema structured output.

OpenAI uses strict `json_schema` structured output.

This is not function calling. Function calling is intended for asking a model
to select and invoke an application tool. NoteFlow is requesting typed content,
so structured output is the more appropriate mechanism.

Backend validation now rejects:

1. Missing or additional top-level fields.
2. Empty section arrays.
3. Missing or additional section fields.
4. Empty headings or Markdown.
5. Unsupported section types.
6. Confidence outside `[0, 1]`.
7. Non-string warning values.
8. Leaked source tags or missing source grounding.

The pipeline may repair superficial JSON escaping before validation, but repaired
content must still pass the complete backend contract.

### PDF To Markdown

PDF-to-Markdown does not ask the model to produce one opaque Markdown document.

```text
PDF layout/text extraction
  + cropped visual regions
  + typed VLM result
  -> deterministic backend Markdown renderer
```

The VLM result contract is:

```text
transcription
description
latex
code
uncertainty
search_text
```

Gemini and OpenAI now both receive an explicit schema. The Worker verifies the
exact field set, string types, and that at least one useful content field is
non-empty. The Markdown renderer then decides whether the result is text,
formula, code, table, diagram, or decorative material.

This keeps page markers, headings, code fences, formulas, figures, deduplication,
and quality warnings under backend control.

## 2. HyDE Strategy

HyDE means Hypothetical Document Embeddings.

It is used only when the original query is short or has very little information.

Examples that trigger HyDE:

```text
PMF
这个是什么   (non-English low-information query: "what is this")
explain this concept please
```

Specific questions do not trigger it:

```text
Why does list_cp_bad create a shallow copy of linked list nodes?
Explain Theorem 4.4.10 Taylor inequality remainder bound.
```

Flow:

```text
original user query
  -> ambiguity/length detector
  -> optional Gemini/OpenAI hypothetical passage
  -> embed original query
  -> embed hypothetical passage
  -> combine vector candidates using max(original, 0.90 * HyDE)
  -> lexical/exact recall still uses original query
  -> RRF, reranking, filtering, context construction
```

Rules:

1. HyDE text is never evidence.
2. HyDE text is never shown as a citation.
3. HyDE text is not passed to lexical or exact matching.
4. The original query remains the reranker and answer-generation question.
5. Provider failure falls back to normal retrieval.
6. Diagnostics expose whether HyDE was triggered/generated, provider, latency,
   and error, but not the generated passage.

Configuration:

```text
HYDE_PROVIDER=auto | disabled | gemini | openai
HYDE_GEMINI_MODEL=gemini-2.5-flash
HYDE_OPENAI_MODEL=gpt-4o-mini
HYDE_TIMEOUT_SECONDS=20
HYDE_MAX_QUERY_TOKENS=8
```

`auto` is the default. The backend selects Gemini when a Gemini key is
available, otherwise OpenAI when an OpenAI key is available, otherwise it skips
HyDE and continues normal retrieval. HyDE is never a user-facing search mode.

## 3. Provider And API-Key Strategy

Provider selection is configuration, not request code.

| Capability | Gemini | OpenAI | Local |
|---|---:|---:|---:|
| Vision | Implemented | Implemented | Reserved |
| AI Notes | Implemented | Implemented | Reserved |
| Embedding Worker | Implemented | Implemented | Reserved |
| Query Embedding API | Implemented | Implemented | Reserved |
| HyDE | Implemented | Implemented | Not required |
| External reranker | Implemented | Reserved | Reserved |

Environment variables:

```text
VISION_PROVIDER
NOTES_PROVIDER
EMBEDDING_PROVIDER
HYDE_PROVIDER
RETRIEVAL_RERANKER_PROVIDER

GEMINI_API_KEY
OPENAI_API_KEY

GEMINI_VISION_MODEL
OPENAI_VISION_MODEL
GEMINI_NOTES_MODEL
OPENAI_NOTES_MODEL
GEMINI_EMBEDDING_MODEL
OPENAI_EMBEDDING_MODEL
HYDE_GEMINI_MODEL
HYDE_OPENAI_MODEL
GEMINI_RERANK_MODEL
```

An embedding corpus is tied to its provider and model. Query embedding must use
the same provider/model as the stored vectors. Switching embedding provider
therefore requires generating a new corpus; the existing content hash prevents
unnecessary regeneration within the same provider/model.

## 4. Why MCP Is Not Used

MCP is not required for this architecture.

MCP is useful when a model needs standardized runtime access to external tools,
files, databases, or third-party services.

These NoteFlow requirements are internal application concerns:

```text
model-provider selection
API-key configuration
structured response schemas
backend validation
embedding generation
HyDE query expansion
retrieval orchestration
```

Using normal provider interfaces is simpler, easier to test, and avoids turning
internal service calls into model-visible tools.

MCP may become useful later if NoteFlow allows an answer agent to query external
course systems, GitHub repositories, calendars, or institutional data sources.
It should not be introduced merely to call Gemini or OpenAI.

## 5. Failure Behavior

| Failure | Behavior |
|---|---|
| Invalid note JSON/schema | Retry according to note policy, then fail only that source group. |
| Invalid VLM JSON/schema | Record region failure and retry according to vision policy. |
| HyDE provider unavailable | Continue with original-query retrieval. |
| Query embedding unavailable | Continue with lexical/exact channels. |
| External reranker unavailable | Keep deterministic RRF order. |
| Every retrieval channel unavailable | Return explicit retrieval failure. |

## 6. Tests

The codebase includes tests for:

1. Strict note response validation.
2. Strict vision response validation.
3. Provider schema shape.
4. HyDE trigger decisions.
5. HyDE-disabled fallback.
6. Retrieval-channel degradation.
7. Formula, theorem, and code exact recall.
8. Search and retrieval scope isolation.
