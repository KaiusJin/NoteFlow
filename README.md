# NoteFlow

NoteFlow is publicly visible for reference and review only.

## Current Technical Specification

See [docs/technical/NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md](docs/technical/NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md)
for the current source-of-truth workflow: upload, Spring Boot APIs, Redis tasks,
PDF-to-Markdown, multi-modal visual handling, chunking, resumable AI notes,
storage layout, exported notes, and quality gates.

## Project Plan

See [docs/technical/PROJECT_PLAN.md](docs/technical/PROJECT_PLAN.md) for the product scope, MVP plan,
architecture, technical stack, roadmap, risks, and portfolio positioning.

## Workflow And Architecture

See [docs/technical/WORKFLOW_AND_ARCHITECTURE.md](docs/technical/WORKFLOW_AND_ARCHITECTURE.md) for the
end-to-end user workflow, service responsibilities, data flow, API links, and
implementation phases.

## Database Schema

See [docs/technical/DATABASE_SCHEMA.md](docs/technical/DATABASE_SCHEMA.md) for the first table design covering
users, documents, async tasks, PDF parse results, and document chunks.

## PDF Markdown And Chunk Pipeline

See [docs/technical/PDF_UPLOAD_MARKDOWN_CHUNK_PIPELINE.md](docs/technical/PDF_UPLOAD_MARKDOWN_CHUNK_PIPELINE.md)
for the current upload, PDF-to-Markdown, Markdown-to-chunk, and output storage
pipeline.

## Chunking Strategy

See [docs/technical/CHUNKING_STRATEGY.md](docs/technical/CHUNKING_STRATEGY.md) for the current PDF chunking
pipeline, metadata design, quality assessment, and known limitations.

## Current Implementation

The first backend and worker modules live in:

- [services/api](services/api): Spring Boot API for PDF upload and task tracking.
- [services/worker](services/worker): Python worker for PDF parsing, visual analysis, chunk extraction, and AI notes generation.
- [apps/web](apps/web): Minimal static frontend for PDF upload and task progress.

## Local Development

Start local infrastructure:

```bash
docker compose up -d postgres redis
```

Then run the API from [services/api](services/api) and the worker from
[services/worker](services/worker). The first implemented flow is:

```text
POST /documents
  -> save PDF
  -> create document row
  -> create parse task row
  -> enqueue Redis task
  -> worker parses PDF
  -> worker writes Markdown, visual metadata, parse result, and chunks
  -> user generates resumable AI notes
  -> task becomes COMPLETED
```

## License

Copyright (c) 2026 Kaius Jin. All rights reserved.

This repository is source-available, but it is not open source.

No permission is granted to use, copy, modify, merge, publish, distribute,
sublicense, sell, commercialize, or create derivative works from this code or
any portion of it without explicit prior written permission from the copyright
holder.

Viewing this repository on GitHub does not grant any license to use the code.
