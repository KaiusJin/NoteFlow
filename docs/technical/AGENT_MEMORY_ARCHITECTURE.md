# NoteFlow Agent Memory Architecture

Status: final-state description of the current implementation (2026-07-06).
This document describes the conversation memory subsystem: short-term memory
(sliding window + rolling summary compression), long-term memory (extraction,
consolidation, recall), multi-conversation management, global user
preferences, per-conversation source scoping, and how memory maintenance
coordinates with the main pipelines.

Code: `services/worker/noteflow_worker/memory/`. Data contracts align with the
accepted schema in `MULTI_TURN_CONVERSATIONAL_RAG_ARCHITECTURE.md`
(`rag_conversations` / `rag_messages` / `rag_memories`, the structured summary
schema, and the memory-type whitelist).

---

## 1. Architecture

### 1.1 Memory hierarchy

```text
┌──────────────────────────────────────────────────────────────┐
│ WorkingContext (assembled per turn, read-only, zero LLM calls)│
│                                                               │
│  ① Rolling summary  active_summary   ← compressed older turns │
│  ② Sliding window   window           ← recent turns, verbatim │
│  ③ Recalled long-term memories       ← user facts relevant to │
│                                        the current question   │
│  ④ Explicit preferences              ← global settings        │
│                                        (authoritative)        │
│  ⑤ Source scope                      ← PDFs / AI notes the    │
│                                        user selected for this │
│                                        conversation           │
│  + per-section token accounting + diagnostics                 │
└──────────────────────────────────────────────────────────────┘
        ▲ read path (hot, synchronous: 3-4 SQL queries +
        │            at most one query-embedding call)
        │
┌───────┴───────────────────────────────────────────────────────┐
│ ConversationMemoryManager                                      │
│                                                                │
│  build_context()    read path: state → window → recall →      │
│                     assemble                                   │
│  record_turn()      write path: persist message + decide      │
│                     whether maintenance is due                 │
│  run_maintenance()  maintenance path (cold, background,       │
│                     contains ALL LLM calls)                    │
│  conversation/preference/source-scope management facade        │
└───────┬────────────────────────────────────────────────────────┘
        │ maintenance path (serialized per conversation
        │ via advisory lock)
        ▼
┌────────────────────────────────────────────────────────────────┐
│ Summary compression            Long-term memory extraction     │
│  backlog > high-water mark?     new dialogue tokens ≥ gate?    │
│  split(retain, evict)           extract (type whitelist +      │
│  LLM folds evicted turns into    confidence + source ids)      │
│   the previous summary          batch-embed candidates         │
│  optimistic-concurrency write   hash/vector dedupe             │
│   (version CAS)                 ADD / UPDATE(supersede) / SKIP │
│  advance summary watermark      advance extraction watermark   │
│                                 + per-user capacity cap        │
└────────────────────────────────────────────────────────────────┘
```

### 1.2 Storage and execution placement

```text
PostgreSQL (pgvector)                        Redis
  rag_conversations    conversation rows +     queue:document-analysis:priority:2
                       memory-state columns      └─ MAINTAIN_CONVERSATION_MEMORY
  rag_messages         append-only messages          (background priority; never
  rag_conversation_summaries  summary audit           competes with parsing or
  rag_memories         long-term memories +           interactive answering)
                       embedding column       ▲
  rag_user_preferences global settings        │ enqueued by the conversation
                                              │ service when record_turn
Worker (main.py)                              │ reports maintenance_needed
  MaintainConversationMemoryPipeline ─────────┘ (or executed inline)
```

Internal module dependencies (pure logic strictly separated from SQL/network,
so the core is testable without a database):

```text
manager.py ──┬─ window.py         window selection / compression split (pure)
             ├─ summarizer.py     rolling-summary prompt/schema/validation
             ├─ extraction.py     memory-candidate prompt/schema/validation
             ├─ consolidation.py  dedupe/merge decisions (pure)
             ├─ recall.py         recall scoring and budgets (pure)
             ├─ preferences.py    preference whitelist/validation/rendering
             ├─ llm.py            structured JSON client (Gemini/OpenAI,
             │                    shared retry policy)
             ├─ store.py          all SQL (inherits Repository conventions)
             └─ embeddings/providers.py  (reuses existing embedding infra)
```

---

## 2. Inputs and Outputs

### 2.1 Interface inputs

| Entry point | Input | Caller |
|---|---|---|
| `record_turn(conversation_id, user_id, role, content, metadata?)` | One message (role ∈ USER/ASSISTANT/TOOL/SYSTEM_SUMMARY) | Conversation service, twice per turn (user question + assistant answer) |
| `build_context(conversation_id, user_id, current_query, query_embedding?)` | The current question; optionally a precomputed query embedding shared with retrieval to avoid paying for it twice | Conversation service, before answer generation |
| `run_maintenance(conversation_id)` | Conversation id | Background task `MAINTAIN_CONVERSATION_MEMORY` (TaskPayload carries `conversationId`) or inline mode |
| `create/list/rename/set_conversation_status` | Multi-conversation management (list ordered by `last_message_at` DESC; statuses ACTIVE/ARCHIVED/DELETED, soft delete) | Conversation sidebar |
| `set_conversation_sources(conversation_id, user_id, SourceScope)` | Selected PDF document ids + AI-note document ids for this conversation; ownership of every id is verified against `documents.user_id` before writing, foreign/unknown ids are rejected | Source picker UI |
| `get/set/clear_user_preference(user_id, key, value)` | Global explicit preferences; whitelisted keys (ANSWER_LANGUAGE / ANSWER_STYLE / EXPLANATION_DEPTH / EXAMPLE_PREFERENCE / DEFAULT_SEARCH_MODE / LONG_TERM_MEMORY), enum values normalized, free-text values length-capped | Settings page |

### 2.2 Database artifacts

| Table | Content | Writer |
|---|---|---|
| `rag_conversations` | Conversation row (title/status/last_message_at, indexed per user for the sidebar) + memory-state columns: `active_summary(_json)`, `summary_version`, summary watermark `(summary_covers_through_at, message_id)`, extraction watermark + source-scope columns `selected_pdf_document_ids` / `selected_ai_note_document_ids` (JSONB) | Write path (timestamps) / maintenance path (summary and watermarks) / management facade (title, status, scope) |
| `rag_messages` | Append-only message stream with `token_count` (estimated once at write time; every later budget decision reuses it); index `(conversation_id, created_at, id)` | Write path |
| `rag_conversation_summaries` | Full audit trail of every summary version (text + structured JSON + coverage + provider/model) | Maintenance path |
| `rag_memories` | Long-term memories: type, content, `content_hash`, confidence, status (ACTIVE/SUPERSEDED/EXPIRED), source message, `embedding vector` + provider/model, access stats, `expires_at` | Maintenance path (read path only updates access stats) |
| `rag_user_preferences` | Global explicit settings, upserted on `(user_id, preference_key)` | Settings facade |

### 2.3 Read-path output: `WorkingContext`

```text
summary_text / summary_json    rolling summary (conversation state,
                               never academic evidence)
window                         sliding-window messages (overlong messages
                               clipped and marked)
recalled_memories              [{record, similarity, score}] — prompt
                               rendering: "Known long-term context about
                               this student (not academic evidence): ..."
preferences                    explicit settings (rendered as authoritative,
                               above inferred preferences; the
                               LONG_TERM_MEMORY switch never enters prompts)
source_scope                   per-conversation source restriction (empty =
                               unrestricted; non-empty = a hard retrieval
                               filter on document_id + source_domain)
window/summary/memory_token_count   per-section token accounting for the
                                    upstream prompt compiler
diagnostics                    summary version, clipped/excluded messages,
                               recall mode and counts
```

### 2.4 Maintenance-path output: `MaintenanceReport`

Whether summarization ran and its new version, evicted message count, whether
extraction ran, candidate count, ADD/UPDATE/SKIP counts, and an error list.
The pipeline logs it as structured JSON and derives task success from it.

---

## 3. Case Matrix

### 3.1 Short-term memory (window and compression)

| Case | Detection | Handling |
|---|---|---|
| Normal follow-up | Unsummarized backlog ≤ trigger | Window = messages after the summary watermark, greedy newest-first within `MEMORY_WINDOW_MAX_TOKENS`, capped by `MAX_TURNS` |
| Budget full but too few turns | Over token budget with < `MIN_TURNS` selected | **min_turns outranks the token budget**: a follow-up never loses its immediate antecedent |
| Single huge message (pasted page of notes) | One message > `MESSAGE_MAX_TOKENS` | Proportionally clipped with an explicit truncation marker — **clipped, not dropped**; id recorded in diagnostics |
| History exceeds high-water mark | Unsummarized tokens > `SUMMARY_TRIGGER_TOKENS` (3200) | Maintenance triggered: newest `RETAIN_TOKENS` (1400) stay verbatim, older turns are evicted into the summary — high/low water hysteresis keeps compression from running every turn |
| Incremental folding | Evicted set non-empty | LLM input = **previous summary JSON + evicted messages only** (cost ∝ new content, independent of total conversation length); output is the structured schema (topics/goals/definitions/unresolved/sources/importantMessageIds/narrative) |
| Summary cites nonexistent message ids | importantMessageIds ⊄ input ids | Validation fails → retried as a stochastic error; on final failure the summary is not written, the watermark stays, next run retries |
| Two workers compress concurrently | `summary_version` CAS mismatch | Optimistic concurrency: the losing write returns False and is treated as a benign lost race |

### 3.2 Long-term memory (extract → consolidate → recall)

| Case | Detection | Handling |
|---|---|---|
| Not enough new dialogue | USER/ASSISTANT tokens since extraction watermark < `EXTRACTION_MIN_NEW_TOKENS` (120) | No LLM call ("thanks" does not trigger extraction) |
| Durable fact worth remembering | LLM extraction | Type whitelist (USER_PREFERENCE / LEARNING_GOAL / KNOWN_DIFFICULTY / COURSE_CONTEXT / EXPLICIT_FACT); each candidate carries confidence, source message id, optional TTL |
| Inferred sensitive traits (health/ethnicity/religion/…) | Prompt hard constraint + type whitelist as a second line of defense | Never stored; non-whitelisted types fail validation outright |
| Nothing worth remembering | Empty memories array | A valid answer: watermark advances, output is not forced |
| Low-confidence candidate | confidence < `EXTRACTION_MIN_CONFIDENCE` | Dropped |
| Verbatim duplicate | Normalized `content_hash` matches an ACTIVE row of the same type | SKIP (cheapest check runs first) |
| Semantic duplicate | Cosine ≥ `DEDUP_THRESHOLD` (0.90) | Higher-confidence candidate → UPDATE (old row SUPERSEDED + linked); otherwise SKIP |
| Fact evolves ("geometric distributions are fine now") | Cosine ∈ [`UPDATE_THRESHOLD` (0.78), 0.90) | UPDATE: new row supersedes the old; history stays auditable |
| Genuinely new fact | Cosine < 0.78 | ADD |
| Time-bound fact ("midterm on July 20") | ttlDays > 0 | `expires_at` written; recall filters expired rows automatically |
| Memory bloat | ACTIVE count > `MAX_ACTIVE_PER_USER` (400) | Lowest-value rows (by confidence, then last access) set EXPIRED; storage stays bounded |
| Normal recall | Query embedding available | pgvector cosine top-`CANDIDATE_LIMIT` → composite score = 0.6·similarity + 0.2·exponential recency decay (14-day half-life) + 0.2·confidence → similarity floor 0.55 → count and token double budget |
| Embedding provider disabled/failed | No query vector | **Degraded recall**: recent high-confidence memories (`FALLBACK_LIMIT`), diagnostics report `recency_fallback` |
| Embedding provider/model switched | Vector space mismatch | Recall and vector dedupe only compare rows from the same provider/model (cross-space distances are meaningless); old memories remain reachable via exact-hash dedupe and the degraded recall path |
| Access-stat update fails | UPDATE exception | Diagnostics only; never breaks the read path |

### 3.3 Multi-conversation, preferences, and source scope

| Case | Detection | Handling |
|---|---|---|
| User opens multiple chats | Distinct `conversation_id`s | Summary/watermarks/window/source scope are all conversation-scoped; long-term memories are shared per `user_id` (cross-conversation personalization is a feature, not a leak) |
| Sidebar listing | `list_conversations` | `(user_id, last_message_at DESC)` index; archived conversations hidden by default; DELETED is a soft delete, no data destruction |
| Explicit vs learned preferences | `rag_user_preferences` vs `USER_PREFERENCE` memories | Both enter the context but render separately; explicit settings are marked authoritative and outrank inferred facts on conflict |
| User disables long-term memory | `LONG_TERM_MEMORY=DISABLED` | Read path skips recall (diagnostics `disabled_by_preference`), maintenance skips extraction — the privacy switch works in both directions |
| Invalid preference key/value | Whitelist + enum + length validation | `set_user_preference` raises; enum values are case-normalized |
| User selects reference sources | `set_conversation_sources` | Every document id is verified against `documents.user_id` first; any unknown/foreign id rejects the whole request — a stale UI or crafted request cannot widen retrieval |
| No sources selected | Both lists empty | `is_unrestricted=True`: the retrieval layer may use every READY document the user owns |
| Scope changed mid-conversation | Conversation-row JSONB update | Takes effect on the next `build_context`; past messages and summaries are unaffected |
| Cross-user access | `state.user_id != requesting user id` | `record_turn`/`build_context` raise `PermissionError` (defense in depth; primary authorization stays in the API layer) |

### 3.4 Failures and concurrency

| Case | Response |
|---|---|
| Transient LLM failure (timeout/429/5xx) or structured-output validation failure | One shared retry policy: ≤ `MEMORY_REQUEST_MAX_ATTEMPTS` attempts, exponential backoff + jitter, 30s cap (validation failures are treated as stochastic model behavior) |
| Deterministic failure (401 / missing key) | Fail fast, no retry |
| Summary succeeds but extraction fails (or vice versa) | Two independently try/excepted phases with **independent watermarks**: the successful side commits, the failed side retries incrementally next run; the report carries all errors |
| Concurrent maintenance on one conversation | `pg_try_advisory_lock(namespace, hashtext(conversation_id))`: the loser returns a skipped report immediately — no queueing, no double spend |
| Worker crash | Watermarks/versions are persisted; re-entry resumes incrementally; re-enqueue is handled by the existing stale-task recovery |
| Invalid message role | `record_turn` raises before touching storage |

---

## 4. Pipeline Coordination and Performance Design

### 4.1 Hot/cold path split (the central performance decision)

- **Zero LLM calls on the read path**: `build_context` = 1 conversation-state
  query + 1 preference query + 1 window query + 1 vector search (+ optionally
  1 query-embedding call, zero when the caller passes one in).
- **Zero LLM calls on the write path**: `record_turn` = 1 INSERT + 2 aggregate
  SUMs (served by the `(conversation_id, created_at, id)` index), producing
  only a "maintenance due" boolean.
- **All LLM/embedding spend lives on the maintenance path**, running as the
  `MAINTAIN_CONVERSATION_MEMORY` background task through the existing
  three-tier priority queue (background tier), governed by the existing
  weighted round-robin and background-slot cap — it never competes with
  parsing or interactive answering. `MEMORY_MAINTENANCE_INLINE=true` switches
  to synchronous execution (dev/test).

### 4.2 Incrementality (cost decoupled from conversation length)

- **Watermarks**: summarization and extraction each keep a composite
  `(covers_through_at, message_id)` watermark; every maintenance run consumes
  only messages after it. The message table is append-only — no flag
  backfilling, no write amplification.
- **Rolling summary folding**: new summary = f(previous summary JSON, evicted
  messages). Full history is never re-read.
- **Token counts estimated once** at write time, stored on the message row,
  reused by every budget decision.

### 4.3 Vector search strategy

- Active memories per user are hard-capped (400), so the candidate set is
  bounded → an exact pgvector scan `ORDER BY embedding <=> query LIMIT k` is
  fast without an ANN index; the `(user_id, status)` B-tree filters first.
- Upgrade path: when the capacity cap is lifted, pin the `embedding` column to
  a fixed-dimension `vector(n)` and add an HNSW index — queries are unchanged.
- Candidate embeddings for dedupe are produced in **one batched call**
  (`embed_texts`), reusing existing concurrency/retry infrastructure.

### 4.4 Concurrency correctness

- Per-conversation advisory lock serializes maintenance; the summary version
  CAS backstops lock failure — under both, the worst case is one skipped
  compression, never a double write.
- `supersede` only touches ACTIVE rows (`WHERE status='ACTIVE'`), so re-runs
  are idempotent.
- All DDL is `CREATE TABLE/INDEX IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS`:
  either the conversation service or the worker may start first.

### 4.5 Boundary with the future conversation service

This subsystem does not own: answer generation, retrieval (hybrid recall),
streaming, or authentication. Per turn, the conversation service runs:

```text
① record_turn(USER message)
② build_context(query, query_embedding)   ← embedding shared with retrieval
③ retrieval: MUST filter by context.source_scope
     (document_embeddings WHERE document_id = ANY(scope), applying the two
      lists per source_domain PDF / AI_NOTE; empty list = unrestricted)
④ answer generation: preferences (authoritative settings) → summary/window
   (conversation state) → recalled_memories (user profile) → retrieved
   evidence, each in its own prompt tier
⑤ record_turn(ASSISTANT message)
⑥ if ①/⑤ returned maintenance_needed → enqueue MAINTAIN_CONVERSATION_MEMORY
```

Summaries and recalled memories are always labeled as conversation state /
user profile in prompts and **must never be cited as academic evidence** —
factual answers still require retrieved source evidence, per the multi-turn
RAG contract.

---

## 5. Key Configuration Reference

```text
# Sliding window
MEMORY_WINDOW_MAX_TOKENS=2400        MEMORY_WINDOW_MIN_TURNS=2
MEMORY_WINDOW_MAX_TURNS=12           MEMORY_WINDOW_MESSAGE_MAX_TOKENS=900
MEMORY_WINDOW_FETCH_LIMIT=96

# Summary compression (high/low water marks)
MEMORY_SUMMARY_TRIGGER_TOKENS=3200   MEMORY_SUMMARY_RETAIN_TOKENS=1400
MEMORY_SUMMARY_MAX_TOKENS=700

# Recall
MEMORY_RECALL_LIMIT=5                MEMORY_RECALL_CANDIDATE_LIMIT=24
MEMORY_RECALL_MIN_SIMILARITY=0.55
MEMORY_RECALL_SIMILARITY_WEIGHT=0.60 MEMORY_RECALL_RECENCY_WEIGHT=0.20
MEMORY_RECALL_CONFIDENCE_WEIGHT=0.20 MEMORY_RECALL_RECENCY_HALF_LIFE_DAYS=14
MEMORY_RECALL_MAX_TOKENS=600         MEMORY_RECALL_FALLBACK_LIMIT=3

# Extraction and consolidation
MEMORY_EXTRACTION_MIN_NEW_TOKENS=120 MEMORY_EXTRACTION_MAX_MESSAGES=40
MEMORY_EXTRACTION_MIN_CONFIDENCE=0.5
MEMORY_DEDUP_SIMILARITY_THRESHOLD=0.90
MEMORY_UPDATE_SIMILARITY_THRESHOLD=0.78
MEMORY_MAX_ACTIVE_PER_USER=400

# LLM (empty values fall back to NOTES_PROVIDER and its models)
MEMORY_LLM_PROVIDER=                 MEMORY_GEMINI_MODEL= / MEMORY_OPENAI_MODEL=
MEMORY_REQUEST_TIMEOUT_SECONDS=60    MEMORY_REQUEST_MAX_ATTEMPTS=3
MEMORY_RETRY_BACKOFF_SECONDS=2

# Maintenance execution
MEMORY_MAINTENANCE_INLINE=false      MEMORY_MAINTENANCE_STALE_AFTER_MINUTES=10
MEMORY_MAINTENANCE_FETCH_LIMIT=200
```
