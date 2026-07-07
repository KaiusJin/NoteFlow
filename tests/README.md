# NoteFlow Tests, Benchmarks, and Quality Evaluation

All Python tests, performance benchmarks, and retrieval-quality evaluation
scripts live in this folder. The only exception is the Spring Boot API's Java
test suite, which must stay under `services/api/src/test` because Gradle owns
that layout; it is indexed here for completeness.

## Layout

```text
tests/
  worker/        Unit tests for the Python worker (PDF pipeline, structured
                 outputs, conversation memory, and Study modules). No database or network needed by default.
  benchmarks/    Performance benchmarks run against a deployment host.
  evaluation/    Retrieval/search quality evaluation against a live API,
                 with its golden-case resources.
  run_worker_tests.sh   One-command worker unit test runner.
```

## Worker unit tests

The worker tests import `noteflow_worker`, so they run with the worker's
virtualenv and `PYTHONPATH` pointing at `services/worker`:

```bash
./tests/run_worker_tests.sh
```

or manually:

```bash
cd services/worker
PYTHONPATH=. .venv/bin/python -m unittest discover -s ../../tests/worker -q
```

| File | Covers |
|---|---|
| `worker/test_pdf_converter_v2.py` | Resource pools, page routing, math normalization/transliteration, layout and boilerplate protection, VLM resume/failover, region budgets, artifact cleanup, synthetic end-to-end PDF |
| `worker/test_structured_outputs.py` | Structured notes/vision response validation |
| `worker/test_conversation_memory.py` | Sliding window, summary compression triggers and validation, memory extraction/consolidation/recall, retry policy, preferences, source scope, multi-user isolation, queue payloads |
| `worker/test_study_modules.py` | Flashcard/quiz schemas, citations, grouping, deduplication, difficulty allocation, grading validation, and SM-2 |
| `worker/test_study_repository_integration.py` | Opt-in PostgreSQL DDL, zero-item checkpoints, idempotency, grading persistence, and score aggregation (`NOTEFLOW_RUN_DB_TESTS=1`) |

## Benchmarks

```bash
cd services/worker
PYTHONPATH=. .venv/bin/python ../../tests/benchmarks/benchmark_pdf_pools.py --pages 48 --workers 1,2,4,8
```

`benchmark_pdf_pools.py` measures MuPDF render throughput per pool size on the
deployment host; use its output to override `PDF_CPU_WORKERS` and related
settings instead of guessing.

Study CPU and token-budget benchmark (never calls a model API):

```bash
PYTHONPATH=services/worker services/worker/.venv/bin/python \
  tests/benchmarks/benchmark_study_modules.py --chunks 5000 --items 300
```

## Quality evaluation

Both scripts run against a live API and a seeded database; they are quality
gates, not unit tests:

```bash
python3 tests/evaluation/evaluate_search_quality.py --base-url http://localhost:8080
python3 tests/evaluation/evaluate_retrieval_quality.py --base-url http://localhost:8080
```

Golden cases live in `tests/evaluation/resources/search-quality-cases.json`.
Add new cases there when a retrieval regression is found; the case file is the
regression corpus.

## Java API tests (external to this folder)

```bash
cd services/api && ./gradlew test
```
