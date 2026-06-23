# Technical Documentation

This folder contains NoteFlow's technical planning and implementation documentation.

## Core Pipeline

| Document | Purpose |
|---|---|
| `PDF_UPLOAD_MARKDOWN_CHUNK_PIPELINE.md` | Current upload, PDF parsing, Markdown generation, chunking, output storage, and type-specific routing. |
| `DOCUMENT_TYPE_PROCESSING_STRATEGY.md` | Document-type-specific Markdown, VLM, and chunk routing strategy. |
| `CHUNKING_STRATEGY.md` | Detailed chunking strategy and quality considerations. |
| `AI_NOTES_GENERATION_PIPELINE.md` | Markdown/chunk-to-AI-notes generation design, schema, prompts, and APIs. |
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
