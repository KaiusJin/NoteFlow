from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from noteflow_worker.memory.models import MemoryCandidate, MemoryRecord


DECISION_ADD = "ADD"
DECISION_UPDATE = "UPDATE"
DECISION_SKIP = "SKIP"


@dataclass(frozen=True)
class ConsolidationDecision:
    action: str
    candidate: MemoryCandidate
    existing: MemoryRecord | None
    similarity: float
    reason: str


def memory_content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.casefold()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def cosine_similarity(first: list[float], second: list[float]) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    dot = sum(a * b for a, b in zip(first, second))
    norm_first = math.sqrt(sum(a * a for a in first))
    norm_second = math.sqrt(sum(b * b for b in second))
    if norm_first == 0.0 or norm_second == 0.0:
        return 0.0
    return dot / (norm_first * norm_second)


def decide_consolidation(
    candidate: MemoryCandidate,
    candidate_embedding: list[float] | None,
    existing_records: list[MemoryRecord],
    *,
    dedup_threshold: float,
    update_threshold: float,
) -> ConsolidationDecision:
    """Decide how a candidate merges into the user's existing memories.

    Cheap-to-expensive ordering: exact normalized-text match first, vector
    similarity second. Vector comparison only considers records embedded with
    a matching vector space (same provider/model); records from an older
    embedding configuration can still be exact-deduplicated but never
    vector-merged, which avoids nonsense cross-space similarities.
    """
    candidate_hash = memory_content_hash(candidate.content)
    same_type = [record for record in existing_records if record.memory_type == candidate.memory_type]

    for record in same_type:
        if record.content_hash == candidate_hash:
            return ConsolidationDecision(DECISION_SKIP, candidate, record, 1.0, "exact_duplicate")

    best_record: MemoryRecord | None = None
    best_similarity = 0.0
    if candidate_embedding:
        for record in same_type:
            if not record.embedding:
                continue
            similarity = cosine_similarity(candidate_embedding, record.embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_record = record

    if best_record is not None and best_similarity >= dedup_threshold:
        if candidate.confidence > best_record.confidence:
            return ConsolidationDecision(
                DECISION_UPDATE, candidate, best_record, best_similarity, "duplicate_with_higher_confidence"
            )
        return ConsolidationDecision(DECISION_SKIP, candidate, best_record, best_similarity, "semantic_duplicate")
    if best_record is not None and best_similarity >= update_threshold:
        return ConsolidationDecision(DECISION_UPDATE, candidate, best_record, best_similarity, "refines_existing_memory")
    return ConsolidationDecision(DECISION_ADD, candidate, None, best_similarity, "new_memory")
