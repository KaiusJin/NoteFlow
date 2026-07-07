#!/usr/bin/env python3
"""CPU/token-budget benchmark for Study grouping, dedupe and SM-2 (no paid API calls)."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from noteflow_worker.db.repository import TextChunk
from noteflow_worker.config import settings
from noteflow_worker.study.common import allocate_item_targets, build_source_groups, is_near_duplicate
from noteflow_worker.study.models import ReviewState
from noteflow_worker.study.srs import schedule_review


def run_benchmark(chunk_count: int = 5000, item_count: int = 300) -> dict:
    chunks = [TextChunk(i // 8 + 1, i, (f"topic {i} theorem formula " * 60), token_count=240, id=str(i))
              for i in range(chunk_count)]
    started = time.perf_counter()
    groups = build_source_groups(chunks, 2400, 3600)
    grouping_seconds = time.perf_counter() - started

    existing = [f"What is concept {i}?" for i in range(item_count)]
    started = time.perf_counter()
    duplicate_hits = sum(is_near_duplicate(f"What is concept {i}?", existing, 0.98) for i in range(item_count))
    dedupe_seconds = time.perf_counter() - started

    state = ReviewState("user", "card", "NEW", 2.5, 0, 0, None, None, None)
    now = datetime.now(timezone.utc)
    started = time.perf_counter()
    for _ in range(100_000):
        schedule_review(state, "GOOD", now)
    scheduling_seconds = time.perf_counter() - started
    source_tokens = sum(chunk.token_count or 0 for chunk in chunks)
    flashcard_targets = allocate_item_targets(groups, settings.flashcards_per_1000_source_tokens,
                                              settings.flashcards_max_per_document)
    quiz_targets = allocate_item_targets(groups, settings.quiz_questions_per_1000_source_tokens,
                                         settings.quiz_max_questions_per_document)
    return {"chunkCount": chunk_count, "sourceTokens": source_tokens, "sourceGroupCount": len(groups),
            "estimatedGenerationApiCalls": len(groups), "groupingSeconds": round(grouping_seconds, 6),
            "maximumConfiguredOutputTokenBudget": len(groups) * settings.study_max_output_tokens,
            "plannedFlashcardCandidates": sum(flashcard_targets.values()),
            "plannedQuizCandidates": sum(quiz_targets.values()),
            "dedupeItems": item_count, "dedupeHits": duplicate_hits, "dedupeSeconds": round(dedupe_seconds, 6),
            "reviewSchedules": 100_000, "schedulingSeconds": round(scheduling_seconds, 6)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=5000)
    parser.add_argument("--items", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(args.chunks, args.items), indent=2))
