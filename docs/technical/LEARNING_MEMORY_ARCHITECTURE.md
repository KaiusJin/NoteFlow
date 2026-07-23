# Learning Memory Architecture

## Scope

The production system implements the complete four-phase Learning Memory loop requested for NoteFlow:

```text
Quiz / Flashcard / explicit feedback
  -> append-only learning event
  -> deterministic mastery + mistake + review update
  -> compact indexed read model
  -> Agent planner tools
```

It deliberately does not ask an LLM to assign mastery. Goals, preferences,
artifacts, review scheduling, planning, corrections, experiments, and topic
relationships all build on the same deterministic event foundation.

## Data model

- `learning_events` is the raw, immutable evidence log. The unique key
  `(workspace_id, external_event_id, topic_key)` makes retries and duplicate
  queue delivery harmless.
- `topic_learning_memory` is the current per-document topic state. It stores
  mastery, confidence, attempts, correctness, hint/latency signals, streaks,
  trend, review schedule, and a monotonic version.
- `mistake_memory` groups deterministic mistake type/summary fingerprints and
  counts repeated occurrences.
- `learning_memory_history` provides versioned mastery trends and algorithm provenance.
- `learning_goals` and `learning_preferences` store explicit planning context;
  behavioral preferences remain hidden until at least five observations.
- `learning_artifact_links` prevents duplicate Note/Quiz/Flashcard generation,
  while `learning_topic_edges` forms a bounded cross-document topic graph.
- `learning_study_plans`, `learning_memory_corrections`, and
  `learning_strategy_experiments` persist planner decisions, auditable user
  corrections/expiration, and stable strategy assignments.

Topic strings are NFKC-normalized, whitespace-folded, and lower-cased for keys,
while the latest display spelling is retained. Raw events are sufficient to
rebuild derived state after a future algorithm version change.

## Write and concurrency design

Event insertion and read-model updates share one database transaction. A write
first claims its event key with `INSERT ... ON CONFLICT DO NOTHING`; only the
winner updates topic and mistake state. The topic update is one atomic PostgreSQL
UPSERT. API and Worker writers also acquire the same sorted per-topic transaction
advisory locks. This keeps normal writes, corrections, and raw-event rebuilds in
one serialization domain without blocking unrelated topics.

Quiz answers use stable keys `quiz-answer:<answer-id>:v1`. Both synchronous
objective grading in Spring and asynchronous free-text grading in the Python
Worker emit the same event shape. Flashcard reviews accept a client `eventId`
and serialize SM-2 updates per `(workspace, card)` with a transaction advisory
lock. The review API requires `eventId`; the event is claimed before SM-2 state
is changed, so a retry cannot advance the schedule twice. Explicit Agent feedback
receives its own caller-supplied or generated ID.

The deterministic v1 evidence rules are intentionally simple:

- correct Quiz answers increase mastery; hard and unhinted evidence weighs more;
- incorrect answers decrease mastery, create mistake evidence, and schedule an
  earlier review;
- Flashcard `AGAIN/HARD/GOOD/EASY` grades have increasing evidence and intervals;
- explicit `CONFUSED` and `MASTERED` feedback is strong evidence;
- note/activity events are weak evidence and cannot imply mastery by themselves.

Mastery is clamped to `[0,1]`; confidence rises with accumulated evidence.
Each update, including the first event for a topic, maintains stability, lapse count, calibration error, and a
history point. Successful reviews use growing stability intervals; failures
reduce stability and schedule near-term review.

Expired topics are automatically reactivated by new evidence. Low-confidence
expiration runs daily as well as through the administrative endpoint. Corrections
require an expected version and update with an atomic `WHERE version = ?` guard.

## API and Agent surface

```text
POST /learning-memory/events
POST /learning-memory/feedback
GET  /learning-memory/profile
GET  /learning-memory/weak-topics
GET  /learning-memory/due-reviews
GET  /learning-memory/topics/{topic}/explanation
PUT  /learning-memory/goals
PUT  /learning-memory/preferences/{key}
POST /learning-memory/artifacts
GET  /learning-memory/topic-graph
POST /learning-memory/corrections
POST /learning-memory/study-plans
POST /learning-memory/expiration/run
POST /learning-memory/topics/{topic}/recalculate
POST /learning-memory/experiments/{key}/assign
```

The Agent receives four first-class tools:

- `get_learning_profile`
- `get_weak_topics`
- `get_due_reviews`
- `record_learning_feedback`

The extended Planner catalog also includes goal/preference management, Artifact
Memory lookup/linking, recursive Topic Graph retrieval, mastery trends,
optimistic-lock corrections, and deterministic dynamic study-plan generation.
Plans honor the active goal's document scope and deadline urgency, prioritize its
topics, use explicit or sufficiently repeated per-topic practice preferences,
reuse existing artifacts, and never exceed the requested time budget. A stable
experiment assignment now selects due-first versus weakness-first ordering, so
strategy experiments affect execution rather than only recording metadata.

Quiz answer events carry client-provided response time and hint usage. Note open
and update paths emit bounded activity events for already-mapped document topics.
Artifact interactions update reuse counters, and repeated Quiz/Flashcard behavior
is sampled into per-topic preferences without creating a workspace-wide hot row.

Read tools query only the small derived tables and return bounded topic context,
never the full raw event log. This keeps planner latency and prompt size stable
as history grows.

## Performance

Indexes cover workspace-time event audit, artifact lookup, weak-topic ranking,
due-review retrieval, and repeated-mistake ranking. Connection pools and
PostgreSQL row-level serialization provide backpressure under load; unrelated
topics update independently.

On the local development PostgreSQL instance, the checked-in benchmark with 32
client threads and all unique writes contending on one topic measured:

| Path | Throughput | p50 | p95 |
|---|---:|---:|---:|
| 300 unique event + history writes | 644/s | 44.88 ms | 69.79 ms |
| 300 duplicate deliveries | 1,728/s | 18.06 ms | 18.87 ms |
| 1,000 compact profile reads | 4,611/s | 6.75 ms | 8.14 ms |

The benchmark is a worst-case same-topic contention run after enabling rebuild-safe
topic serialization. It verified exactly 300 raw events, 300 history points, and
300 derived attempts after the duplicate storm. These figures are local evidence, not production SLOs; run the
same benchmark against deployment-sized PostgreSQL before setting an SLO.

```bash
PYTHONPATH=services/worker services/worker/.venv/bin/python \
  tests/benchmarks/benchmark_learning_memory.py --events 500 --workers 32 --reads 2000
```

## Verification

```bash
gradle -p services/api test
./tests/run_worker_tests.sh
NOTEFLOW_RUN_DB_TESTS=1 PYTHONPATH=services/worker \
  services/worker/.venv/bin/python -m unittest \
  tests.worker.test_study_repository_integration -v
```

The database suite includes a 48-delivery, 12-thread duplicate storm and asserts
that event count, attempts, incorrect count, version, and mistake occurrence all
remain exactly one. Java database regressions additionally cover duplicate
Flashcard review IDs, rebuild/live-write locking, first-failure metrics,
optimistic correction versions, and expiration reactivation.
