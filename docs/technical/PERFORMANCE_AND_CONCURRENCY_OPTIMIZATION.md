# Performance And Concurrency Optimization

This document records implemented performance fixes and remaining scale work.

## Implemented Hot-Path Fixes

### `/documents`

The document list no longer performs per-document status queries. It now:

1. Loads the user's documents once.
2. Loads latest AI-note statuses for all document ids in one repository query.
3. Loads embedding tasks for all document ids in one repository query.
4. Loads embedding-ready document ids with one grouped SQL query.

This removes the prior `1 + N * status queries` shape from the frontend's
global polling path.

### `/notes`

`GET /notes` is now a pure read. RAW PDF Markdown note synchronization moved to:

1. The worker write path when `document_markdown_documents` is saved.
2. Startup backfill in `LibraryMigrationRunner` for existing markdown documents.

This avoids doing document scans and conditional note writes on every library
list request.

### `/tasks`

`GET /tasks` now returns active tasks plus the most recent 100 tasks, ordered by
creation time. This keeps the existing frontend contract while bounding payload
growth from historical task rows.

### Frontend Polling

The global documents/tasks poll is adaptive:

1. Active tasks keep the prior 1.5 second cadence.
2. Idle screens back off to 5 seconds.
3. Hidden browser tabs pause the poll and recheck every 5 seconds.

Turn-specific polling, such as conversation answers and quiz grading, keeps its
local polling behavior.

### Parse Detail Loading

The General parsed-output view now loads in two stages:

1. Summary, chunks, assets, and layout blocks.
2. Visual regions, VLM results, markdown pages, and markdown preview.

The second stage uses bounded query parameters so long PDFs do not force full
document payloads into the first render.

## Implemented Indexes

`PerformanceSchemaManager` creates indexes for the hot list paths:

```sql
documents(user_id, created_at DESC)
tasks(user_id, created_at DESC)
tasks(user_id, status, created_at DESC)
tasks(document_id, task_type, created_at DESC)
notes(user_id, updated_at DESC)
notes(source_document_id, source_kind, created_at)
```

`RetrievalSchemaManager` creates a pgvector ANN index per observed embedding
dimension. For Gemini's 3072-dimensional embeddings, it uses `halfvec` because
pgvector HNSW `vector` indexes are limited to 2000 dimensions:

```sql
USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
WHERE embedding IS NOT NULL AND embedding_dimension = 3072
```

Vector queries now filter by `embedding_dimension` and cast to the matching
`vector(n)` or `halfvec(n)` distance expression so PostgreSQL can use the
dimension-specific index.

## Retrieval Concurrency

The retrieval channel executor default concurrency was raised from 3 to 12.
HyDE is now disabled by default and must be explicitly enabled with
`HYDE_PROVIDER`, preventing retrieval HTTP requests from synchronously calling
an LLM merely because an API key is configured.

## Worker Queue Reliability

The Python worker now uses Redis delivery leases instead of consuming tasks as
fire-and-forget list pops:

1. Priority-queue pops register the raw payload under
   `queue:document-analysis:processing:payloads`.
2. A matching deadline is stored in
   `queue:document-analysis:processing:deadlines`.
3. Running tasks refresh their lease while they remain active.
4. Completed tasks `ack` the lease.
5. Expired leases are reclaimed and requeued to their priority list.

This removes the crash window where an already-popped Redis item could
disappear until database stale-task recovery later noticed it. The database
stale recovery paths remain as a second line of defense.

Default worker slot counts were also raised from 3/1 to 4/2 for total/background
concurrency. Deployments can still override them with environment variables.

## Remaining Larger Work

- Full parse artifacts still have full-response compatibility paths; the
  frontend preview uses bounded requests, but dedicated cursor/page APIs would
  be cleaner for very large documents.
- No production load test profile exists yet. Current verification covers
  compile/tests, startup, index creation, and local endpoint timing on the
  developer dataset.
