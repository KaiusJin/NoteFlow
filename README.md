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

For the shared Quiz/Flashcard generation boundary and Agent tool adapters, see
[docs/technical/LOCAL_AGENTIC_STUDY_ARCHITECTURE.md](docs/technical/LOCAL_AGENTIC_STUDY_ARCHITECTURE.md).
That document records the earlier local-only model; the hosted identity decision
is superseded by
[ADR-001](docs/architecture/ADR-001-cloud-web-supabase-identity.md).

## Database Schema

See [docs/technical/DATABASE_SCHEMA.md](docs/technical/DATABASE_SCHEMA.md) for the table design covering
documents, async tasks, PDF parse results, and document chunks. Hosted NoteFlow
uses Supabase Auth identities and personal workspaces; local development uses a
compatible seeded workspace profile.

## PDF Markdown And Chunk Pipeline

See [docs/technical/PDF_UPLOAD_MARKDOWN_CHUNK_PIPELINE.md](docs/technical/PDF_UPLOAD_MARKDOWN_CHUNK_PIPELINE.md)
for the current upload, PDF-to-Markdown, Markdown-to-chunk, and output storage
pipeline.

## Chunking Strategy

See [docs/technical/CHUNKING_STRATEGY.md](docs/technical/CHUNKING_STRATEGY.md) for the current PDF chunking
pipeline, metadata design, quality assessment, and known limitations.

## Current Implementation

The first backend and worker modules live in:

- [services/api](services/api): Spring Boot API for document upload, task tracking (SSE), library/folders, study modules, conversational RAG, and hybrid retrieval.
- [services/worker](services/worker): Python worker for PDF parsing, visual analysis, chunk extraction, embeddings, AI notes generation, study modules, conversation memory, and the tool-calling agent.
- [apps/web-v2](apps/web-v2): Primary React/TypeScript PWA for Supabase sign-in, documents, grounded search/Agent, offline-safe notes, flashcards, and quizzes. It is deployable to Cloudflare Pages.
- [apps/web](apps/web): Legacy local workbench retained while remaining editor-only capabilities are migrated.
- [apps/editor](apps/editor): Source for the legacy vendored CodeMirror editor bundle. Generated hashed assets are not part of the hosted PWA architecture.

Supporting directories:

- [infra](infra): `docker compose` infrastructure — PostgreSQL (pgvector), Redis, and the `observability` profile (OpenTelemetry collector, Tempo, Prometheus, Grafana).
- [tests](tests): worker unit/integration tests, API tests (in `services/api/src/test`), Playwright browser security specs, benchmarks, and retrieval-quality evaluation.
- [docs/technical](docs/technical/README.md): full technical documentation index, including the 2026-08-30 full-project review and its remediation roadmap.

## Local Development

Start local infrastructure:

```bash
docker compose up -d postgres redis

# Optional observability stack (Grafana at http://localhost:3000):
docker compose --profile observability up -d
```

Then run the API from [services/api](services/api), the worker from
[services/worker](services/worker), and the PWA from [apps/web-v2](apps/web-v2).
Supabase and free-tier deployment setup is documented in
[SUPABASE_AUTH_SETUP.md](docs/deployment/SUPABASE_AUTH_SETUP.md) and
[FREE_CLOUD_ARCHITECTURE.md](docs/deployment/FREE_CLOUD_ARCHITECTURE.md).
The main implemented flow is:

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
