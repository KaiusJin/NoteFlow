from __future__ import annotations

import json
from dataclasses import dataclass, replace

from noteflow_worker.config import settings
from noteflow_worker.db.repository import vector_literal
from noteflow_worker.memory.models import SourceScope
from noteflow_worker.pdf.parser import estimate_tokens


EVIDENCE_CLIP_MARKER = "\n[... evidence truncated ...]"


@dataclass(frozen=True)
class Evidence:
    """One retrieved, citable source passage."""

    index: int
    source_domain: str
    source_object_type: str
    source_object_id: str
    document_id: str
    document_title: str
    title: str
    page_start: int | None
    page_end: int | None
    text: str
    snippet: str
    similarity: float


def search_evidence(
    store,
    user_id: str,
    query_embedding: list[float],
    embedding_provider: str,
    embedding_model: str,
    scope: SourceScope,
) -> list[Evidence]:
    """Scope-filtered vector recall over document_embeddings.

    Scope semantics match the product surface: empty scope means every READY
    document the user owns; a non-empty scope means exactly the selected
    sources (PDF chunks restricted to the PDF list, AI-note sections to the
    AI-note list). Provider/model matching prevents cross-space distances.
    """
    literal = vector_literal(query_embedding)
    filters = [
        "e.embedding IS NOT NULL",
        "e.embedding_provider = %s",
        "e.embedding_model = %s",
    ]
    params: list = [literal, user_id, embedding_provider, embedding_model]
    if not scope.is_unrestricted:
        filters.append(
            "((e.source_domain = 'PDF' AND e.document_id = ANY(%s::uuid[]))"
            " OR (e.source_domain = 'AI_NOTE' AND e.document_id = ANY(%s::uuid[])))"
        )
        params.extend([scope.pdf_document_ids, scope.ai_note_document_ids])
    params.extend([literal, settings.answer_evidence_candidate_limit])

    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT e.source_domain, e.source_object_type, e.source_object_id, e.document_id,
                   e.embedding_text, e.text_preview, e.metadata_json,
                   1 - (e.embedding <=> %s::vector) AS similarity
            FROM document_embeddings e
            JOIN documents d ON d.id = e.document_id AND d.user_id = %s AND d.status = 'READY'
            WHERE {' AND '.join(filters)}
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()

    titles = store.load_document_titles([str(row["document_id"]) for row in rows])
    candidates = [evidence_from_row(dict(row), titles) for row in rows]
    return apply_evidence_budgets(candidates)


def evidence_from_row(row: dict, document_titles: dict[str, str]) -> Evidence:
    metadata = parse_json_safe(row.get("metadata_json")) or {}
    document_id = str(row["document_id"])
    return Evidence(
        index=0,
        source_domain=row["source_domain"] or "PDF",
        source_object_type=row["source_object_type"] or "",
        source_object_id=str(row["source_object_id"]),
        document_id=document_id,
        document_title=document_titles.get(document_id, ""),
        title=str(metadata.get("title") or ""),
        page_start=as_int(metadata.get("pageStart")),
        page_end=as_int(metadata.get("pageEnd")),
        text=row.get("embedding_text") or row.get("text_preview") or "",
        snippet=(row.get("text_preview") or "")[:600],
        similarity=float(row["similarity"]),
    )


def apply_evidence_budgets(candidates: list[Evidence]) -> list[Evidence]:
    """Similarity floor, per-item clipping, then count and token caps."""
    selected: list[Evidence] = []
    total_tokens = 0
    for candidate in candidates:
        if candidate.similarity < settings.answer_evidence_min_similarity:
            continue
        if len(selected) >= settings.answer_evidence_top_k:
            break
        clipped = clip_evidence_text(candidate, settings.answer_evidence_item_max_tokens)
        tokens = estimate_tokens(clipped.text)
        if selected and total_tokens + tokens > settings.answer_evidence_max_tokens:
            continue
        selected.append(replace(clipped, index=len(selected)))
        total_tokens += tokens
    return selected


def clip_evidence_text(evidence: Evidence, max_tokens: int) -> Evidence:
    tokens = estimate_tokens(evidence.text)
    if tokens <= max_tokens:
        return evidence
    keep_chars = max(1, int(len(evidence.text) * (max_tokens / max(1, tokens))))
    return replace(evidence, text=evidence.text[:keep_chars].rstrip() + EVIDENCE_CLIP_MARKER)


def parse_json_safe(value):
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def as_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
