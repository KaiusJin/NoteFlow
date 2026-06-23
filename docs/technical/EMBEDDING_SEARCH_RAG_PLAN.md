# Embedding, Semantic Search, And RAG Plan

This document defines the next NoteFlow implementation phase after PDF-to-Markdown, chunking, and AI notes generation.

Goal:

```text
User natural-language query
  -> query embedding
  -> search both PDF-derived chunks and AI note sections
  -> return source-grounded results
  -> later use results for RAG answers
```

## 1. Product Decision

Search should retrieve two user-visible source domains:

```text
PDF
AI Note
```

Internally, these map to more precise source objects:

| User-visible source | Internal object | Source chain |
|---|---|---|
| `PDF` | `DOCUMENT_CHUNK` | PDF -> layout/VLM -> Markdown -> chunk |
| `AI Note` | `AI_NOTE_SECTION` | PDF -> Markdown -> chunk -> AI note section |

There are no raw-PDF chunks in the current system. `DOCUMENT_CHUNK` means a chunk generated from parsed PDF Markdown, layout blocks, and VLM-enriched visual content.

## 2. Provider Strategy

MVP provider:

```text
GeminiEmbeddingProvider
```

Reserved interfaces:

```text
OpenAIEmbeddingProvider
LocalEmbeddingProvider
DisabledEmbeddingProvider
```

Configuration:

```text
EMBEDDING_PROVIDER=gemini
GEMINI_API_KEY=...
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
EMBEDDING_BATCH_SIZE=16
EMBEDDING_MAX_CONCURRENT_REQUESTS=5

OPENAI_API_KEY=...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

LOCAL_EMBEDDING_MODEL=bge-small-en-v1.5
```

Provider interface:

```python
class EmbeddingProvider:
    provider_name: str
    model: str
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...
```

Rules:

1. Do not hard-code Gemini into the pipeline.
2. Store provider, model, and dimension with every embedding row.
3. Use batching where the provider supports it.
4. Use a bounded request pool for network providers. The current default allows at most 5 simultaneous embedding API calls per embedding task.
5. Hash source text so unchanged sources are not re-embedded.
6. If provider is disabled or missing a key, fail the embedding task explicitly.

## 3. Database Schema

Table:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE document_embeddings (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL,
  source_domain VARCHAR(32) NOT NULL,
  source_object_type VARCHAR(64) NOT NULL,
  source_object_id UUID NOT NULL,
  embedding_provider VARCHAR(64) NOT NULL,
  embedding_model VARCHAR(128) NOT NULL,
  embedding_dimension INTEGER NOT NULL,
  content_hash VARCHAR(128) NOT NULL,
  embedding_text TEXT NOT NULL,
  text_preview TEXT,
  embedding vector,
  metadata_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(source_domain, source_object_type, source_object_id, embedding_provider, embedding_model)
);
```

Allowed source values:

```text
source_domain:
  PDF
  AI_NOTE

source_object_type:
  DOCUMENT_CHUNK
  AI_NOTE_SECTION
```

Recommended metadata:

```json
{
  "pageStart": 12,
  "pageEnd": 13,
  "title": "Geometric Distribution",
  "chunkIndex": 24,
  "noteId": "uuid",
  "noteVersion": 3,
  "tokenCount": 420
}
```

Vector index:

```sql
CREATE INDEX idx_document_embeddings_vector
ON document_embeddings
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
```

Use the correct vector dimension for the selected provider/model. If the local database image does not support pgvector yet, install/enable pgvector before implementing search.

## 4. Embedding Source Text

### 4.1 PDF / DOCUMENT_CHUNK

Source table:

```text
document_chunks
```

Embedding text:

```text
section title
page range
chunk content
useful metadata labels
```

Recommended text template:

```text
Source: PDF
Pages: {page_start}-{page_end}
Section: {section_title}
Type: {chunk_type}

{content}
```

### 4.2 AI Note / AI_NOTE_SECTION

Source table:

```text
document_ai_note_sections
```

Embedding text:

```text
heading
section type
page range
section markdown
```

Recommended text template:

```text
Source: AI Note
Pages: {page_start}-{page_end}
Heading: {heading}
Type: {section_type}

{markdown}
```

Only embed sections from the latest `READY` AI note by default. Older note versions can remain searchable later if we add version filters.

## 5. Embedding Generation Workflow

Initial worker flow:

```text
Document is READY
  -> create GENERATE_EMBEDDINGS task
  -> worker loads document chunks
  -> worker loads latest READY AI note sections if available
  -> build embedding source records
  -> skip unchanged records by content_hash
  -> call Gemini embedding provider in batches
  -> each batch uses a bounded concurrent request pool
  -> upsert document_embeddings
  -> mark task complete
```

Inputs:

```text
document_id
document_chunks
latest READY document_ai_notes
document_ai_note_sections
embedding provider config
```

Outputs:

```text
document_embeddings
tasks status/progress
```

Suggested task steps:

```text
GENERATING_EMBEDDINGS
EMBEDDING_PDF_CHUNKS
EMBEDDING_AI_NOTE_SECTIONS
COMPLETED
FAILED
```

Idempotency:

1. Compute `content_hash = sha256(embedding_text)`.
2. If an embedding row exists with the same provider, model, source object, and hash, skip.
3. If content changed, update embedding, hash, preview, metadata, and `updated_at`.

Concurrency:

1. `EMBEDDING_BATCH_SIZE` controls how many source texts enter one provider batch.
2. `EMBEDDING_MAX_CONCURRENT_REQUESTS` controls how many HTTP embedding requests can be in flight at the same time inside that batch.
3. The default is 5 concurrent requests. This speeds up large documents while still leaving a clear throttle for API rate limits.
4. Provider implementations must preserve result order so the returned embedding at index `i` still belongs to source text `i`.

## 6. Search API

Endpoints:

```http
POST /search
POST /documents/{documentId}/search
```

Request:

```json
{
  "query": "Why can variance be written as E[X^2] - E[X]^2?",
  "topK": 8,
  "mode": "MIXED",
  "pdfDocumentIds": ["uuid"],
  "aiNoteDocumentIds": ["uuid"]
}
```

Defaults:

```text
topK = 8
mode = MIXED
```

Search modes:

| Mode | Meaning | Scope |
|---|---|---|
| `PDF` | Original PDF-derived knowledge only | `source_domain = PDF` |
| `AI_NOTE` | Organized AI note sections only | `source_domain = AI_NOTE` |
| `MIXED` | Both PDF-derived chunks and AI notes | `PDF + AI_NOTE` |
| `CUSTOM` | User manually selects PDFs and/or AI Notes | `pdfDocumentIds` and `aiNoteDocumentIds` |

Important naming rule:

```text
User UI: PDF, AI Note
Internal source_domain: PDF, AI_NOTE
Internal source_object_type: DOCUMENT_CHUNK, AI_NOTE_SECTION
```

Response:

```json
{
  "query": "Why can variance be written as E[X^2] - E[X]^2?",
  "results": [
    {
      "sourceDomain": "PDF",
      "sourceObjectType": "DOCUMENT_CHUNK",
      "sourceObjectId": "uuid",
      "pageStart": 120,
      "pageEnd": 121,
      "title": "Variance Shortcut Formula",
      "snippet": "...",
      "score": 0.84,
      "metadata": {}
    },
    {
      "sourceDomain": "AI_NOTE",
      "sourceObjectType": "AI_NOTE_SECTION",
      "sourceObjectId": "uuid",
      "pageStart": 120,
      "pageEnd": 121,
      "title": "Variance Formula",
      "snippet": "...",
      "score": 0.81,
      "metadata": {}
    }
  ]
}
```

Search behavior:

1. Generate query embedding with the active embedding provider.
2. Filter `document_embeddings` by provider, model, selected source domains, and selected document IDs.
3. Rank by pgvector cosine distance.
4. Return page/title/snippet fields from `metadata_json` and `text_preview`.
5. Return mixed PDF and AI Note results.

Endpoint differences:

1. `POST /documents/{documentId}/search` is for a single document detail workflow.
2. `POST /search` is for global and custom cross-document search.
3. `CUSTOM` mode should normally use `POST /search`, because the user can select multiple PDFs and notes.

## 7. RAG Answer Workflow

RAG is a later layer on top of semantic search.

Endpoint draft:

```http
POST /documents/{documentId}/ask
```

Flow:

```text
question
  -> query embedding
  -> retrieve PDF + AI Note results
  -> build answer context
  -> LLM answer
  -> cite PDF pages/chunks whenever possible
```

Answer grounding rule:

1. AI Note sections can help organize and explain.
2. PDF/DOCUMENT_CHUNK results are preferred as final evidence citations.
3. If an answer is based only on AI Note and no PDF source is retrieved, mark citation confidence lower.

## 8. Frontend Search UI

Initial UI:

1. Global search button in the documents section.
2. Per-document search button in each document row.
3. Search input.
4. Search type selector:

```text
Mixed | Original PDF | AI Note | Custom selected files
```

5. Custom selected files panel:

```text
Document title | [ ] PDF | [ ] AI Note
```

Rules:

1. Disable PDF checkbox if the document is not `READY`.
2. Disable AI Note checkbox if `aiNoteStatus` is not `READY`.
3. `CUSTOM` sends `pdfDocumentIds` and `aiNoteDocumentIds`.
4. Non-custom per-document search sends mode/query/topK to `/documents/{documentId}/search`.

Result cards show:

```text
source label
title/heading
page range
snippet
score
```

4. Clicking a PDF result opens chunk/Markdown/page context.
5. Clicking an AI Note result scrolls or opens the note section.

Do not expose internal names like `DOCUMENT_CHUNK` to normal users. Show `PDF` and `AI Note`.

## 9. Implementation Plan

### Step 1: Schema

1. Ensure pgvector is enabled.
2. Replace/upgrade placeholder `document_embeddings` schema.
3. Add indexes and uniqueness constraints.

### Step 2: Provider Abstraction

1. Add `noteflow_worker/embeddings/providers.py`.
2. Implement `GeminiEmbeddingProvider`.
3. Add placeholder `OpenAIEmbeddingProvider`.
4. Add placeholder `LocalEmbeddingProvider`.
5. Add `DisabledEmbeddingProvider`.

### Step 3: Repository Methods

1. Load PDF chunk embedding sources.
2. Load latest READY AI note section embedding sources.
3. Upsert embeddings.
4. Query embeddings by similarity.

### Step 4: Worker Pipeline

1. Add `GenerateEmbeddingsPipeline`.
2. Add `GENERATE_EMBEDDINGS` handling to worker.
3. Add script to backfill embeddings for existing documents.

### Step 5: API

1. Add endpoint to trigger embeddings if needed.
2. Add `POST /documents/{documentId}/search`.
3. Return mixed PDF/AI Note results.

### Step 6: Frontend

1. Add search panel.
2. Add source filters.
3. Render results with page/snippet/source.

### Step 7: RAG

1. Add `POST /documents/{documentId}/ask`.
2. Reuse search retrieval.
3. Build source-grounded answer prompt.
4. Return answer with citations.

## 10. Quality Gates

Embedding generation is acceptable when:

1. Every `document_chunks` row for a READY document has a `PDF/DOCUMENT_CHUNK` embedding.
2. Every latest READY `document_ai_note_sections` row has an `AI_NOTE/AI_NOTE_SECTION` embedding.
3. Re-running embedding generation skips unchanged source objects.
4. Search returns mixed source results for broad conceptual queries.
5. Search can be filtered to PDF-only or AI Note-only.
6. Results include page range and readable snippet.

RAG is acceptable when:

1. Answers cite retrieved PDF pages/chunks.
2. AI Note citations are labeled separately from PDF citations.
3. The answer says when retrieved evidence is insufficient.
4. The system does not answer from model memory alone.

## 11. Current Open Questions

1. Exact Gemini embedding model and vector dimension to use in production.
2. Whether to auto-run embeddings immediately after parsing, after AI notes generation, or both.
3. Whether to include older AI note versions in search.
4. Whether visual regions should become a third embedded source later.
5. Whether search should return results grouped by source or globally ranked first.
