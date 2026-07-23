# Local Agentic Study Architecture

This document is the source of truth for NoteFlow's Study generation entry
points and local persistence model as of 2026-07-22.

## 1. Product boundaries

Quiz and Flashcards each have two entry points but one generation capability:

```text
Structured Study UI -----------+
                               +--> QuizGenerationService --> Quiz Set
Agent generate_quiz -----------+

Structured Study UI --------------------+
                                        +--> FlashcardGenerationService --> Deck
Agent generate_flashcards -------------+
```

The dedicated Study sections are for explicit, fully configured batch work and
for managing, editing and studying persistent artifacts. The Agent is the
natural-language orchestration layer: it chooses context from the conversation,
retrieved chunks, selected sections and later quiz-attempt feedback.

Agent tools never maintain a second copy of quiz or flashcard data in chat.
They return a durable artifact id, task id, preview metadata and `artifactUrl`.
The resulting set/deck is stored in the same tables and appears in Study.

## 2. Shared generation path

The Spring domain services own:

- request and source-scope validation;
- exact generation configuration persistence;
- title and version assignment;
- idempotent reuse of an identical in-flight request;
- artifact/task creation in one transaction;
- task-to-artifact binding and enqueue-after-commit.

The structured endpoints call these services directly. The Python Agent tool
adapter calls typed local internal endpoints backed by the same services. It no
longer inserts `quiz_sets`, `flashcard_decks`, `tasks`, or
`study_task_targets` itself.

```text
POST /documents/{id}/quiz-sets -------------------+
POST /internal/study/quiz-generations ------------+--> QuizGenerationService

POST /documents/{id}/flashcard-decks -------------+
POST /internal/study/flashcard-generations --------+--> FlashcardGenerationService
```

The worker remains the execution layer. It loads the persisted scope/options,
retrieves matching chunks, calls the configured model, validates grounded
structured output and persists items/checkpoints into the artifact selected by
`study_task_targets`.

## 3. Local single-workspace persistence

NoteFlow has no account or login domain and no `users` table. All authoritative
data remains on the owner's computer:

- uploaded PDFs under `NOTEFLOW_UPLOAD_DIR` (default `services/api/storage`);
- metadata, notes, conversations, artifacts and attempts in the local
  PostgreSQL named volume `noteflow_postgres_data`;
- Redis is a local delivery queue, not the source of truth;
- API keys and AI settings are installation-local.

`NOTEFLOW_LOCAL_WORKSPACE_ID` is a stable installation namespace, not a user
identity. Existing `user_id` columns are retained temporarily as compatibility
scope columns so current installations can migrate without rewriting every row
and queue payload. They have no foreign key to an account table. New product
logic must not interpret them as authentication or expose account switching.

On API startup `LocalPersistenceSchemaManager` drops foreign keys that reference
the obsolete table, then drops `users`. Existing content rows remain intact.

## 4. Consequences

Removed responsibilities:

- signup/login/session flows;
- password, OAuth and token storage;
- cross-user authorization filters;
- server account lifecycle and account-based data deletion;
- cloud sync assumptions.

Still required locally:

- stable document/artifact ids and referential integrity;
- filesystem/database backup and restore as one unit;
- safe schema migrations;
- localhost-only service exposure for a packaged desktop build;
- secure handling of locally stored provider keys;
- crash-safe queues, idempotency, checkpoints and retries.

The current repository is a local development service stack, not yet a bundled
single-process desktop distribution. A packaged release must either bundle and
manage PostgreSQL/Redis/API/worker lifecycles or replace infrastructure pieces
with embedded equivalents while preserving the same domain contracts.

## 5. Next Agent workflow capabilities

The tool names intentionally express targeted context use:

- `generate_quiz`
- `generate_flashcards`

The retrieval tools remain separate so the bounded Agent loop can search,
inspect a section and then create artifacts. Future feedback workflows can add
`get_recent_quiz_attempts` and `get_weak_topics` without creating another Quiz
generator; they culminate in the same shared generation services.
