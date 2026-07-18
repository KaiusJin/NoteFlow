# Technical Documentation

This folder contains NoteFlow's technical planning and implementation documentation.

## Current Source Of Truth

Start here:

| Document | Purpose |
|---|---|
| `NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md` | Current end-to-end technical specification for upload, API interfaces, storage, PDF-to-Markdown, chunking, AI notes, concurrency, resume, and export. |
| `PDF_CONVERTER_V2_ARCHITECTURE.md` | Final-state architecture of the worker pipeline (PDF -> Markdown -> chunks -> AI notes -> embeddings): architecture diagram, inputs/outputs, case-handling matrix, and pipeline coordination. It supersedes older parser/router details in supporting documents. |

The documents below are supporting references. If a supporting document conflicts with `NOTE_FLOW_PIPELINE_TECHNICAL_SPEC.md`, treat the specification as current and update the older document.

## Core Pipeline References

| Document | Purpose |
|---|---|
| `PDF_UPLOAD_MARKDOWN_CHUNK_PIPELINE.md` | Current upload, PDF parsing, Markdown generation, chunking, output storage, and type-specific routing. |
| `PDF_TO_RAW_MARKDOWN_STRATEGY.md` | Historical pre-V2 code audit covering the old strategy and the gaps that motivated the converter rewrite. |
| `DOCUMENT_TYPE_PROCESSING_STRATEGY.md` | Document-type-specific Markdown, VLM, and chunk routing strategy. |
| `CHUNKING_STRATEGY.md` | Detailed chunking strategy and quality considerations. |
| `AI_NOTES_GENERATION_PIPELINE.md` | Markdown/chunk-to-AI-notes generation design, schema, prompts, APIs, resume behavior, and offline rebuild. |
| `EMBEDDING_SEARCH_RAG_PLAN.md` | Implemented Gemini-first embeddings and initial dual-source semantic search, plus the RAG direction. |
| `HYBRID_RETRIEVAL_CONTEXT_PIPELINE.md` | Next implementation phase: vector/lexical recall, RRF, reranking, deduplication, context construction, citations, and retrieval evaluation. |
| `STRUCTURED_OUTPUT_HYDE_PROVIDER_ARCHITECTURE.md` | Structured model responses, backend validation, HyDE query expansion, Gemini/OpenAI provider configuration, and MCP decision. |
| `AGENT_MEMORY_ARCHITECTURE.md` | Implemented conversation vertical: persistent REST/messages, grounded answer worker, durable citations, short-term window, rolling summary, long-term memory, source scope, and maintenance coordination. |
| `STUDY_MODULES_ARCHITECTURE.md` | Worker implementation and service contracts for independent Flashcards/Quiz sections, source-grounded generation, resumable grading, SM-2, concurrency, token budgets, tests, and benchmarks. |
| `LIBRARY_FOLDERS_ARCHITECTURE.md` | Implemented Folders/library architecture: nested folders, unified notes, source-kind smart views, PDF Markdown note sync, editor integration, and API contracts. |
| `PERFORMANCE_AND_CONCURRENCY_OPTIMIZATION.md` | Implemented hot-path performance fixes: batched document statuses, pure-read note lists, bounded task lists, reduced polling, parse-preview payload bounds, retrieval executor tuning, and hot indexes. |
| `MULTI_TURN_CONVERSATIONAL_RAG_ARCHITECTURE.md` | Source of truth for the next phase: multi-turn conversation state, prompts, streaming, context compression, memory, LangGraph, local SLM deployment, and speculative decoding. |
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
