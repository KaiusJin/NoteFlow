from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from noteflow_worker.db.repository import TextChunk
from noteflow_worker.pdf.parser import estimate_tokens


@dataclass(frozen=True)
class SourceGroup:
    index: int
    chunks: list[TextChunk]

    @property
    def pages(self) -> list[int]:
        pages: set[int] = set()
        for chunk in self.chunks:
            pages.update(range(chunk.page_start or chunk.page_number, (chunk.page_end or chunk.page_number) + 1))
        return sorted(pages)

    @property
    def chunk_ids(self) -> list[str]:
        return [str(chunk.id) for chunk in self.chunks if chunk.id]

    @property
    def token_count(self) -> int:
        return sum(chunk.token_count or estimate_tokens(chunk.content) for chunk in self.chunks)


def build_source_groups(chunks: list[TextChunk], target_tokens: int, max_tokens: int) -> list[SourceGroup]:
    """Pack ordered chunks without dropping an oversized chunk."""
    groups: list[SourceGroup] = []
    current: list[TextChunk] = []
    tokens = 0
    for chunk in chunks:
        chunk_tokens = chunk.token_count or estimate_tokens(chunk.content)
        if current and tokens + chunk_tokens > max_tokens:
            groups.append(SourceGroup(len(groups), current))
            current, tokens = [], 0
        current.append(chunk)
        tokens += chunk_tokens
        if tokens >= target_tokens:
            groups.append(SourceGroup(len(groups), current))
            current, tokens = [], 0
    if current:
        groups.append(SourceGroup(len(groups), current))
    return groups


def source_text(group: SourceGroup) -> str:
    return "\n\n".join(
        f'<source index="{i}" chunk_id="{chunk.id}" pages="{chunk.page_start or chunk.page_number}-{chunk.page_end or chunk.page_number}">\n'
        f"{chunk.content}\n</source>"
        for i, chunk in enumerate(group.chunks)
    )


def resolve_sources(group: SourceGroup, indexes: list[int]) -> tuple[list[str], list[int]]:
    if not indexes or any(isinstance(index, bool) or index < 0 or index >= len(group.chunks) for index in indexes):
        raise ValueError("Every item must cite valid source indexes from its source group.")
    selected = [group.chunks[index] for index in sorted(set(indexes))]
    ids = [str(chunk.id) for chunk in selected if chunk.id]
    if not ids:
        raise ValueError("Cited chunks do not have persistent IDs.")
    pages: set[int] = set()
    for chunk in selected:
        pages.update(range(chunk.page_start or chunk.page_number, (chunk.page_end or chunk.page_number) + 1))
    return ids, sorted(pages)


def canonical_text(*parts: str) -> str:
    return re.sub(r"\s+", " ", " ".join(parts).casefold()).strip()


def dedupe_hash(*parts: str) -> str:
    return hashlib.sha256(canonical_text(*parts).encode()).hexdigest()


def is_near_duplicate(candidate: str, accepted: list[str], threshold: float) -> bool:
    normalized = canonical_text(candidate)
    return any(SequenceMatcher(None, normalized, canonical_text(item)).ratio() >= threshold for item in accepted)


def allocate_difficulty_targets(
    groups: list[SourceGroup],
    difficulty_counts: dict[str, int],
) -> dict[int, dict[str, int]]:
    """Distribute exact per-difficulty counts across source groups.

    Each difficulty is apportioned independently by token weight using largest
    remainder, so the per-group sums equal the requested totals exactly. Groups
    that receive nothing are omitted (they are not sent to the model), which is
    what lets a small requested count honor its exact size even when the
    document has many source groups.
    """
    if not groups:
        return {}
    weights = {group.index: max(1, group.token_count) for group in groups}
    total_weight = sum(weights.values())
    result: dict[int, dict[str, int]] = {group.index: {} for group in groups}
    for difficulty, count in difficulty_counts.items():
        if count <= 0:
            continue
        raw = {index: count * weight / total_weight for index, weight in weights.items()}
        base = {index: math.floor(value) for index, value in raw.items()}
        remainder = count - sum(base.values())
        ranked = sorted(raw, key=lambda index: (raw[index] - base[index], weights[index]), reverse=True)
        for index in ranked[:remainder]:
            base[index] += 1
        for index, assigned in base.items():
            if assigned:
                result[index][difficulty] = assigned
    return {index: mix for index, mix in result.items() if sum(mix.values()) > 0}


def allocate_item_targets(groups: list[SourceGroup], per_1000_tokens: float, configured_max: int) -> dict[int, int]:
    """Allocate exact group counts, preserving at least one item per source group."""
    if not groups:
        return {}
    effective_max = max(configured_max, len(groups))
    desired_total = max(len(groups), round(sum(group.token_count for group in groups) / 1000 * per_1000_tokens))
    target_total = min(effective_max, desired_total)
    result = {group.index: 1 for group in groups}
    remaining = target_total - len(groups)
    if not remaining:
        return result
    token_total = sum(group.token_count for group in groups) or len(groups)
    raw = {group.index: remaining * group.token_count / token_total for group in groups}
    for index, value in raw.items():
        result[index] += math.floor(value)
    leftover = target_total - sum(result.values())
    for index in sorted(raw, key=lambda key: (raw[key] - math.floor(raw[key]), -key), reverse=True)[:leftover]:
        result[index] += 1
    return result
