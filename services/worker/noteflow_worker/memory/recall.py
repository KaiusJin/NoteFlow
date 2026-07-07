from __future__ import annotations

import math
from datetime import datetime, timezone

from noteflow_worker.config import settings
from noteflow_worker.memory.models import MemoryRecord, RecalledMemory
from noteflow_worker.pdf.parser import estimate_tokens


def rank_recalled_memories(
    scored_candidates: list[tuple[MemoryRecord, float]],
    *,
    now: datetime | None = None,
) -> list[RecalledMemory]:
    """Rank candidate memories by a weighted composite of evidence signals.

    similarity comes from the vector search; recency decays exponentially with
    a configurable half-life so stale facts fade without a hard cutoff; the
    extraction confidence keeps weakly-grounded memories from dominating.
    Final output is limited by both count and token budget so the prompt cost
    of long-term memory stays bounded regardless of how much a user has stored.
    """
    now = now or datetime.now(timezone.utc)
    ranked: list[RecalledMemory] = []
    for record, similarity in scored_candidates:
        if similarity < settings.memory_recall_min_similarity:
            continue
        score = (
            settings.memory_recall_similarity_weight * similarity
            + settings.memory_recall_recency_weight * recency_factor(record, now)
            + settings.memory_recall_confidence_weight * max(0.0, min(1.0, record.confidence))
        )
        ranked.append(RecalledMemory(record=record, similarity=similarity, score=score))
    ranked.sort(key=lambda item: item.score, reverse=True)
    return apply_budgets(ranked)


def apply_budgets(ranked: list[RecalledMemory]) -> list[RecalledMemory]:
    selected: list[RecalledMemory] = []
    total_tokens = 0
    for item in ranked:
        if len(selected) >= settings.memory_recall_limit:
            break
        tokens = estimate_tokens(item.record.content)
        if selected and total_tokens + tokens > settings.memory_recall_max_tokens:
            continue
        selected.append(item)
        total_tokens += tokens
    return selected


def recency_factor(record: MemoryRecord, now: datetime) -> float:
    reference = record.updated_at or record.created_at
    if reference is None:
        return 0.5
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - reference).total_seconds() / 86400.0)
    half_life = max(0.1, settings.memory_recall_recency_half_life_days)
    return math.exp(-math.log(2.0) * age_days / half_life)


def render_memories_for_prompt(recalled: list[RecalledMemory]) -> str:
    """Prompt-facing rendering; memories are user state, never source evidence."""
    if not recalled:
        return ""
    lines = ["Known long-term context about this student (not academic evidence):"]
    lines.extend(
        f"- [{item.record.memory_type}] {item.record.content}"
        for item in recalled
    )
    return "\n".join(lines)


def recalled_token_count(recalled: list[RecalledMemory]) -> int:
    if not recalled:
        return 0
    return estimate_tokens(render_memories_for_prompt(recalled))
