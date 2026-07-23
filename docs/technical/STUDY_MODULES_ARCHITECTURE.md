# Flashcards and Quiz Study Modules Architecture

## 1. Scope and product boundary

Flashcards and Quiz are durable study artifacts shown as independent sections
beside AI Notes. They are also creatable through targeted Agent tools, but both
entry points use the same Spring generation services and persistent models. The
Agent contributes conversational scope and orchestration; it does not own a
second artifact store.

This implementation delivers the Python worker core:

- whole-document, on-demand flashcard and quiz generation;
- source-grounded structured output and durable source-group checkpoints;
- resumable generation with bounded concurrency and deduplication;
- rubric-based free-text quiz grading;
- deterministic SM-2 scheduling primitives and persistence;
- PostgreSQL DDL owned by `StudyRepository`;
- unit tests, opt-in database integration tests, and a cost/performance benchmark.

Spring REST resources and the web Study Workspace now implement the service and
interaction contracts specified in section 9.

### 1.1 Implementation status

| Layer | Status | Evidence |
|---|---|---|
| Worker generation, grading, SRS | Implemented | pipelines and `study/` package |
| PostgreSQL schema/checkpoints/leases | Implemented | `StudyRepository` + DB integration tests |
| Shared Java task enums | Implemented | `TaskType` and `TaskStep` |
| Java Study REST/service layer | Implemented | `com.noteflow.study` JDBC service and controller |
| Web Flashcards/Quiz sections | Implemented | independent responsive Study Workspace |
| Export endpoints/renderers | Not implemented | persistence is export-ready only |

The Study workflow is available end to end when PostgreSQL, Redis, Spring API,
worker and web app are running. Generation still requires a configured provider.

## 2. Runtime architecture

```text
Document detail Study section
  -> Spring transaction creates Deck / Quiz Set / Attempt + Task
  -> after commit, TaskDispatchService enqueues Redis payload
  -> Python worker routes task
       GENERATE_FLASHCARDS -> GenerateFlashcardsPipeline
       GENERATE_QUIZ       -> GenerateQuizPipeline
       GRADE_QUIZ_ATTEMPT  -> GradeQuizAttemptPipeline
  -> bounded structured Gemini/OpenAI request
  -> validate enum, fields, rubric, confidence and source indexes
  -> persist each accepted item and source-group checkpoint
  -> update progress, quality report and READY/PARTIAL/FAILED state
```

The Java API remains the authority for local-workspace scope, request validation,
task creation and enqueue-after-commit. The worker assumes that a matching
`GENERATING` deck/set already exists. It will not silently create user-facing
artifacts when a task is malformed.

## 3. Task contracts

| Task | Target | Priority | Payload-specific field |
|---|---|---|---|
| `GENERATE_FLASHCARDS` | document and latest generating deck | user-visible | none |
| `GENERATE_QUIZ` | document and latest generating quiz set | user-visible | none |
| `GRADE_QUIZ_ATTEMPT` | submitted attempt | user-visible | `attemptId` |

Generation input is the ordered `document_chunks` collection. Chunks are packed
to a target token budget without dropping an oversized chunk. Each model-visible
chunk receives a zero-based local index. Returned citations are rejected unless
all indexes exist in that source group and resolve to persistent chunk IDs.

`study_generation_checkpoints` records completion independently of generated
items. This matters when a valid group produces zero accepted items: item-count
inference would otherwise regenerate that group forever. Checkpoints use:

```text
(generation_type, set_id, source_group_index) -> COMPLETED | FAILED
```

Items additionally have unique `(set_id, source_group_index, item_index)` and
`(set_id, dedupe_hash)` constraints. Re-delivery is therefore idempotent at both
the group and item layers.

## 4. Structured generation and validation

Gemini uses `response_schema`; OpenAI uses strict JSON Schema. Both return every
field, including optional-by-business-semantics fields as empty strings/arrays.
Provider output is rejected and retried when it contains malformed JSON, unknown
fields, missing fields, invalid enums, empty required text, invalid confidence,
or ungrounded source indexes.

### Flashcards

Supported types are `DEFINITION`, `CONCEPT_QA`, `FORMULA`, `THEOREM`, and
`CLOZE`. A cloze card must contain `clozeText`. Every card stores front/back,
difficulty, topic, hint, tags, citations, confidence and warnings. Markdown and
LaTeX are passed through unchanged.

### Quiz questions

Supported types are `CONCEPTUAL`, `CALCULATION`, `PROOF`, `MULTIPLE_CHOICE`,
`SHORT_ANSWER`, and `TRUE_FALSE`. Every rubric is an ordered set of points and
weights whose sum must equal the question's points. MCQs require at least two
options, a correct answer present in the options, and exactly one rationale for
each distractor. Calculation/proof step requirements are reinforced by prompts.

The configured difficulty mix is normalized, then converted to exact integer
counts per source-group request with largest-remainder allocation. The final
quality report records both the configured target and actual persisted counts.

## 5. Resume, partial failure and stale recovery

Provider calls run concurrently, but persistence and progress updates happen as
each future finishes. A failed group writes a FAILED checkpoint and never deletes
successful groups. The set becomes:

- `READY` when every group completes;
- `PARTIAL` when at least one group completed and at least one failed;
- `FAILED` when no group completed.

Re-enqueueing reads COMPLETED checkpoints and requests only missing/failed groups.
Worker startup atomically claims stale study tasks with `FOR UPDATE SKIP LOCKED`,
increments retry count and re-enqueues them. `study_task_targets` retains an
attempt ID for grading-task recovery because the shared task table is document
oriented.

`study_execution_leases` adds a database-wide, expiring lease per deck, quiz set,
or grading attempt. This prevents duplicate delivery across worker processes from
issuing duplicate paid model requests. Every completed group/answer renews the
lease; crashes become recoverable after `STUDY_LEASE_SECONDS`.

## 6. Quiz grading

The Java service must grade objective questions synchronously and persist
`graded_by=AUTO` before queueing free-text work. The worker grades only
`CONCEPTUAL`, `CALCULATION`, `PROOF`, and `SHORT_ANSWER` answers whose
`graded_by` is null.

Each free-text request contains exactly one question, answer key, ordered rubric,
maximum points and student response. Validation enforces one boolean per rubric
point and a score inside `[0, max_points]`. The database total is recomputed from
persisted answers; a model-provided aggregate is never trusted. Already graded
answers are skipped, so retries resume at answer granularity.
`isCorrect` is recomputed from awarded-points ratio using
`QUIZ_FREE_TEXT_PASS_THRESHOLD`; the model's boolean cannot contradict the score.

The attempt becomes `COMPLETED` only when every answer has `graded_by`. Otherwise
the task fails with the remaining count and preserves all successful grades.

## 7. SM-2 scheduling

Review state currently retains the compatibility key `(user_id, flashcard_id)`;
in local mode the first value is the stable installation workspace namespace,
not an account. Grades map to classic SM-2 quality:

```text
AGAIN=1, HARD=3, GOOD=4, EASY=5
```

Ease uses the SM-2 equation with a configurable 1.3 floor. AGAIN resets
repetitions and schedules one day; the first two successful intervals default to
one and six days. HARD applies a 0.8 interval modifier and EASY a 1.3 modifier.
Scheduling is deterministic, synchronous, timezone-aware, and performs no LLM
or queue work. Suspended cards reject review updates; reset restores NEW state.

## 8. Performance and API-token controls

There are three concurrency layers:

1. `WORKER_MAX_CONCURRENT_TASKS` limits whole-document work.
2. Flashcard/quiz/grading pool settings limit calls inside one task.
3. `STUDY_GLOBAL_MAX_CONCURRENT_REQUESTS` is a process-wide semaphore preventing
   several simultaneous tasks from multiplying provider pressure.

Input groups default to 2.4k/3.6k tokens for cards and 2.6k/3.8k for quizzes.
Expected item density is explicit (`*_PER_1000_SOURCE_TOKENS`) and output is
capped by `STUDY_MAX_OUTPUT_TOKENS`. Quality reports include source tokens and
estimated generation-call count. They also persist thread-safe aggregate input,
output and total token counts from Gemini/OpenAI usage metadata, including
successful HTTP responses whose model output later fails validation and is retried.
Per-group item targets are allocated across the configured document maximum to
avoid generating items that will only be discarded. Whole-document grounding
takes precedence: if a document has more source groups than the configured item
maximum, the effective maximum expands to one item per group and the quality
report exposes `configuredMaximumExpandedForCoverage=true`.

Near-duplicate filtering uses normalized `SequenceMatcher`. Its bounded maxima
(300 cards, 120 questions) keep quadratic comparison work controlled. The
benchmark measures the worst configured card-set size and 100,000 SM-2 updates.

Run:

```bash
PYTHONPATH=services/worker services/worker/.venv/bin/python \
  tests/benchmarks/benchmark_study_modules.py --chunks 5000 --items 300
```

The benchmark never calls an external API and reports source tokens, projected
generation calls, grouping time, dedupe time and scheduling throughput.

### 8.1 Secret and data-safety controls

- API keys are loaded only from ignored environment files or process variables;
  `.env.example` contains names and safe defaults, never credentials.
- Provider objects exclude `api_key` from their representation, and quality
  reports persist token counts rather than request headers or URLs.
- Gemini keys appear in request URLs because that API requires it, but exception
  normalization never persists the URL.
- Every generation task revalidates READY document ownership and every grading
  task revalidates GRADING attempt ownership inside the worker.
- Source text and student responses are explicitly marked untrusted in prompts;
  instructions embedded in either are not authority.
- Dynamic SQL identifiers are selected only from hard-coded allowlists; values
  always use database parameters.
- Generated artifacts cascade from their parent set; per-user review and attempt
  state remains separate from generated content.

## 9. Required Spring and web contracts

The shared Spring task enums declare all three Study task types and processing
steps. Study services create artifacts and tasks in one transaction and publish
only after commit. They enforce document ownership for every
deck, card, set, question and attempt operation.

Recommended REST surface:

```text
POST /documents/{id}/flashcard-decks
GET  /documents/{id}/flashcard-decks
GET  /flashcard-decks/{id}/cards
GET  /flashcard-decks/{id}/reviews/due
POST /flashcards/{id}/reviews
POST /flashcards/{id}/suspend
POST /flashcards/{id}/reset

POST /documents/{id}/quiz-sets
GET  /documents/{id}/quiz-sets
GET  /quiz-sets/{id}/questions
POST /quiz-sets/{id}/attempts
PUT  /quiz-attempts/{id}/answers/{questionId}
POST /quiz-attempts/{id}/submit
GET  /quiz-attempts/{id}
```

The web app exposes independent `Flashcards` and `Quiz` modules with generation
progress, historical versions and review/attempt flows. No conversation tool
registration is permitted.

The responsive Study Workspace is a separate wide section rather than chat UI.
Each document exposes one entry action; the workspace presents Flashcards and
Quiz as two modules, stacking at the 720px breakpoint. It includes generation
progress/history, due-card review and SM-2 grades, interactive quiz answers,
grading state, scores, feedback, explanations and source-page references.

## 10. Verification

```bash
./tests/run_worker_tests.sh

# Opt-in PostgreSQL DDL/repository integration suite
NOTEFLOW_RUN_DB_TESTS=1 PYTHONPATH=services/worker \
  services/worker/.venv/bin/python -m unittest \
  tests.worker.test_study_repository_integration -v
```

Unit coverage includes schema strictness, enum and rubric validation, grounding,
group completeness, deduplication, exact difficulty allocation and SM-2 state
transitions. Database coverage includes zero-item checkpoints, idempotent inserts,
distribution queries, free-text grade persistence and attempt score recomputation.

## 11. Deliberate boundaries

- Objective MCQ/true-false grading is a synchronous Java responsibility.
- Export rendering is an API/web responsibility; persisted fields preserve all
  data required for Anki TSV, Markdown and printable question/answer documents.
- Weak-topic feedback is stored for the Study UI but is not fed into conversation
  memory in this phase.
- Provider calls are not used in automated tests; production quality evaluation
  requires a separately budgeted golden-document run.
