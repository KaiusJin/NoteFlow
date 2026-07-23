# Agent Tool Platform

## Product boundary

The Agent is the natural-language orchestration layer above Sources,
Workspace, and Study. It does not own a second quiz, flashcard, or note model.
Learning tools call the same generation services as the dedicated pages and
save durable artifacts into Quiz, Flashcards, or Notes.

```text
Structured pages ───────┐
                       ├── shared domain services ── persistent artifacts
Agent tool adapters ────┘
```

Structured pages serve configured whole-document and batch workflows. Agent
tools serve contextual workflows based on the conversation, selected chunks,
prior mistakes, and multi-step analysis.

## Public catalog (36 tools)

| Category | Tools | Responsibility |
|---|---|---|
| Retrieval | `search_sources`, `search_notes`, `search_quiz_history`, `search_flashcards`, `retrieve_related_chunks`, `retrieve_previous_conversation` | Read grounded workspace and learning context without mutation |
| Learning | `generate_quiz`, `generate_flashcards`, `generate_ai_notes`, `generate_summary`, `generate_study_guide`, `generate_examples`, `generate_practice_questions` | Create durable artifacts in their dedicated sections |
| Workspace | `read_markdown`, `edit_markdown`, `insert_section`, `delete_section`, `rewrite_paragraph`, `update_note`, `save_artifact` | Read or change persistent Markdown notes |
| Analytics | `analyze_quiz_performance`, `find_weak_topics`, `estimate_mastery`, `recommend_review_order`, `detect_frequently_wrong_concepts` | Derive learning state from attempts and review history |
| Planning | `create_study_plan`, `break_down_task`, `prioritize_tasks`, `decide_next_action`, `select_documents`, `estimate_time` | Turn learning goals into an ordered, time-bounded workflow |
| Validation | `verify_citation`, `check_coverage`, `detect_hallucination`, `evaluate_generated_quiz`, `retry_generation` | Check grounding and quality, then retry eligible failed/partial generations |

`search_sources` is semantic, citation-producing retrieval over parsed source
content. `search_notes` searches editable Workspace Markdown. The distinction is
intentional.

## Runtime model

The registry is assembled in `conversation/agent.py`; extended adapters live in
`conversation/agent_toolkit.py`. Every tool declares a typed object schema,
sync/async behavior, description, and handler. Arguments are validated again at
runtime before the handler executes. Missing fields, wrong types, enum
violations, and unknown fields fail closed and are recorded in the agent trace.

The bounded plan-act-observe loop permits up to twelve execution steps, a 90-second
wall budget, and a 60,000-token cumulative planning/observation budget. Identical calls are
blocked to prevent loops. Public traces redact secrets and expose tool name,
arguments, latency, outcome, artifact handles, and errors without exposing
private chain-of-thought.

### Tool Orchestrator state machine

The model selects a typed tool from conversation context; deterministic policy
then validates prerequisites the model cannot waive. Workspace mutation
requires a successful `read_markdown` for the same note in the current run.
Summary, study-guide, and example persistence requires non-trivial Markdown and
chunk IDs returned by this run's retrieval.

```text
PLANNING -> EXECUTING -> PLANNING
                    \-> WAITING -> EVALUATING -> PLANNING
                                           \-> REFLECTING -> WAITING
```

The durable state snapshot contains evidence, scratchpad, repeat-call guards,
budgets, pending artifact, and reflection counters. Resumption therefore does
not repeat retrieval or create duplicate artifacts.

### Evaluation and reflection

Every async learning artifact is a mandatory postcondition boundary. The Agent
does not claim completion when generation merely starts. It subscribes to the
background task, leaves the assistant message in `GENERATING`, and resumes when
the task becomes `COMPLETED` or `FAILED`.

Quiz and flashcard postconditions check status, non-empty output, source-group
coverage, grounding, confidence, and duplicates. AI Notes check READY state,
non-empty sections, and section-to-chunk coverage. Failed postconditions create
an `evaluation` trace followed by `reflection`. FAILED/PARTIAL artifacts use
resumable retry; READY artifacts failing quality checks create a refined version
through the original tool. Automatic reflection is capped at two retries.

`agent_run_snapshots` and `agent_task_waits` form the durable continuation
boundary. Completion notification and subscription both check terminal task
state, closing the subscribe-after-completion race. Resume work uses the
interactive `RESUME_AGENT_RUN` task type.

### Live execution trace

Every tool, evaluation, reflection, fallback, and finalization step is
checkpointed to `agent_run_steps` and the assistant message's structured
response. While the message is generating, the browser refreshes the trace in
place, showing phase, tool arguments, observations, status, latency, errors,
retry count, and artifact handles. Internal decision notes remain server-side.

## Persistence and safety

- Quiz and flashcard tools call the shared local generation API and return the
  persisted set/deck plus background task handles.
- Summary, guide, examples, plans, and generic Markdown artifacts are stored in
  Notes, with source-scope metadata where applicable.
- Workspace edits resolve notes in the current local user scope. Exact-text
  edits can use an optimistic Markdown hash to detect concurrent changes.
- `delete_section` refuses to mutate until `confirm=true`; the system prompt
  allows that value only after explicit user confirmation.
- Citation and hallucination checks resolve chunk IDs through an ownership join.
- `retry_generation` only accepts `FAILED` or `PARTIAL` artifacts and reuses
  their stored source scope and generation options.

## Example orchestration

For “make a review plan from what I keep getting wrong,” the Agent can execute:

1. `detect_frequently_wrong_concepts`
2. `select_documents`
3. `search_sources`
4. `generate_quiz`
5. `generate_flashcards`
6. `create_study_plan`
7. `evaluate_generated_quiz` when the async generation is ready

The final response contains previews and links; ongoing management and study
remain in the dedicated sections.
