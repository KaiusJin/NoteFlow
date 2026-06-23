# Technical Documentation

This folder contains NoteFlow's technical planning and implementation documentation.

## Current Source Of Truth

Start here:

| Document | Purpose |
|---|---|
| `NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md` | Current end-to-end technical specification for upload, API interfaces, storage, PDF-to-Markdown, chunking, AI notes, concurrency, resume, and export. |

The documents below are supporting references. If a supporting document conflicts with `NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md`, treat the specification as current and update the older document.

## Core Pipeline References

| Document | Purpose |
|---|---|
| `PDF_UPLOAD_MARKDOWN_CHUNK_PIPELINE.md` | Current upload, PDF parsing, Markdown generation, chunking, output storage, and type-specific routing. |
| `DOCUMENT_TYPE_PROCESSING_STRATEGY.md` | Document-type-specific Markdown, VLM, and chunk routing strategy. |
| `CHUNKING_STRATEGY.md` | Detailed chunking strategy and quality considerations. |
| `AI_NOTES_GENERATION_PIPELINE.md` | Markdown/chunk-to-AI-notes generation design, schema, prompts, APIs, resume behavior, and offline rebuild. |
| `EMBEDDING_SEARCH_RAG_PLAN.md` | Next-phase plan for Gemini-first embeddings, dual-source PDF/AI Note retrieval, semantic search, and RAG. |
| `DATABASE_SCHEMA.md` | Database table design and data ownership. |
| `WORKFLOW_AND_ARCHITECTURE.md` | Product workflow and system architecture. |
| `LOCAL_DEVELOPMENT_RUNBOOK.md` | Local startup steps, URLs, health checks, and `Failed to fetch` troubleshooting. |

## Planning And Audit

| Document | Purpose |
|---|---|
| `PROJECT_PLAN.md` | Project plan and roadmap. |
| `PDF_MARKDOWN_CHUNK_AUDIT_REPORT.md` | Audit of current uploaded documents' Markdown and chunk quality. |

Research summaries that are not core implementation docs live under:

```text
docs/research/
```
