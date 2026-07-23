"""Deterministic postcondition evaluation for Agent-created async artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactEvaluation:
    passed: bool
    retryable: bool
    report: dict


def evaluate_pending_artifact(state, pending: dict) -> ArtifactEvaluation:
    handle = pending.get("handle") or {}
    task_id = str(handle.get("taskId") or pending.get("taskId") or "")
    task = _one(state, "SELECT status,error_message FROM tasks WHERE id=%s", (task_id,))
    if not task:
        return ArtifactEvaluation(False, False, {"reason": "task_not_found", "taskId": task_id})
    if task["status"] not in {"COMPLETED", "FAILED"}:
        return ArtifactEvaluation(False, True, {"reason": "task_not_terminal", "task": task})
    if task["status"] == "FAILED":
        return ArtifactEvaluation(False, True, {"reason": "generation_task_failed", "task": task})

    kind = handle.get("kind")
    if kind == "quiz":
        return _evaluate_quiz(state, str(handle.get("quizSetId") or ""))
    if kind == "flashcards":
        return _evaluate_flashcards(state, str(handle.get("deckId") or ""))
    if kind == "note":
        return _evaluate_note(state, str(handle.get("noteId") or ""))
    return ArtifactEvaluation(False, False, {"reason": "unsupported_artifact_kind", "kind": kind})


def retry_arguments(state, pending: dict) -> tuple[str, dict]:
    """Return the public retry tool and arguments for a failed postcondition."""
    handle = pending.get("handle") or {}
    report = pending.get("evaluation") or {}
    kind = handle.get("kind")
    if kind == "quiz" and report.get("status") in {"FAILED", "PARTIAL"}:
        return "retry_generation", {"artifactType": "QUIZ", "artifactId": str(handle["quizSetId"])}
    if kind == "flashcards" and report.get("status") in {"FAILED", "PARTIAL"}:
        return "retry_generation", {"artifactType": "FLASHCARDS", "artifactId": str(handle["deckId"])}
    # READY-but-low-quality artifacts create a refined version through the
    # original tool; failed/partial sets use the resumable retry endpoint.
    return str(pending.get("rootTool") or pending.get("tool") or "generate_ai_notes"), dict(pending.get("rootArgs") or pending.get("args") or {})


def _evaluate_quiz(state, quiz_id: str) -> ArtifactEvaluation:
    meta = _one(state, """SELECT status,total_source_groups,completed_source_groups,quality_report_json,error_message
      FROM quiz_sets WHERE id=%s AND user_id=%s""", (quiz_id, state.user_id))
    if not meta:
        return ArtifactEvaluation(False, False, {"reason": "quiz_not_found", "quizSetId": quiz_id})
    stats = _one(state, """SELECT COUNT(*) question_count,COUNT(DISTINCT topic) topic_count,
      COUNT(*) FILTER (WHERE source_chunk_ids_json='[]') ungrounded_count,
      COUNT(*) FILTER (WHERE confidence<0.5) low_confidence_count,
      COUNT(*)-COUNT(DISTINCT dedupe_hash) duplicate_count FROM quiz_questions WHERE quiz_set_id=%s""", (quiz_id,)) or {}
    complete = int(meta.get("completed_source_groups") or 0) >= int(meta.get("total_source_groups") or 0)
    failures = [name for name in ("ungrounded_count", "low_confidence_count", "duplicate_count") if int(stats.get(name) or 0)]
    passed = meta["status"] == "READY" and complete and int(stats.get("question_count") or 0) > 0 and not failures
    report = {"artifactType": "QUIZ", "artifactId": quiz_id, **meta, **stats,
              "coverageComplete": complete, "failedChecks": failures, "passed": passed}
    return ArtifactEvaluation(passed, True, report)


def _evaluate_flashcards(state, deck_id: str) -> ArtifactEvaluation:
    meta = _one(state, """SELECT status,total_source_groups,completed_source_groups,quality_report_json,error_message
      FROM flashcard_decks WHERE id=%s AND user_id=%s""", (deck_id, state.user_id))
    if not meta:
        return ArtifactEvaluation(False, False, {"reason": "deck_not_found", "deckId": deck_id})
    stats = _one(state, """SELECT COUNT(*) card_count,COUNT(DISTINCT topic) topic_count,
      COUNT(*) FILTER (WHERE source_chunk_ids_json='[]') ungrounded_count,
      COUNT(*) FILTER (WHERE confidence<0.5) low_confidence_count,
      COUNT(*)-COUNT(DISTINCT dedupe_hash) duplicate_count FROM flashcards WHERE deck_id=%s""", (deck_id,)) or {}
    complete = int(meta.get("completed_source_groups") or 0) >= int(meta.get("total_source_groups") or 0)
    failures = [name for name in ("ungrounded_count", "low_confidence_count", "duplicate_count") if int(stats.get(name) or 0)]
    passed = meta["status"] == "READY" and complete and int(stats.get("card_count") or 0) > 0 and not failures
    report = {"artifactType": "FLASHCARDS", "artifactId": deck_id, **meta, **stats,
              "coverageComplete": complete, "failedChecks": failures, "passed": passed}
    return ArtifactEvaluation(passed, True, report)


def _evaluate_note(state, note_id: str) -> ArtifactEvaluation:
    meta = _one(state, """SELECT n.status,n.quality_report_json,
      (SELECT COUNT(*) FROM document_ai_note_sections s WHERE s.note_id=n.id) section_count,
      (SELECT COUNT(*) FROM document_ai_note_sections s
        WHERE s.note_id=n.id AND COALESCE(s.source_chunk_ids_json,'[]')<>'[]') covered_section_count
      FROM document_ai_notes n JOIN documents d ON d.id=n.document_id
      WHERE n.id=%s AND d.user_id=%s""", (note_id, state.user_id))
    if not meta:
        return ArtifactEvaluation(False, False, {"reason": "note_not_found", "noteId": note_id})
    passed = meta["status"] == "READY" and int(meta.get("section_count") or 0) > 0 and int(meta.get("covered_section_count") or 0) > 0
    report = {"artifactType": "AI_NOTE", "artifactId": note_id, **meta, "passed": passed}
    return ArtifactEvaluation(passed, meta["status"] in {"FAILED", "PARTIAL"}, report)


def _one(state, query: str, params: tuple) -> dict | None:
    with state.store.connect() as conn:
        row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def observation(evaluation: ArtifactEvaluation) -> str:
    return json.dumps({"evaluation": evaluation.report}, ensure_ascii=False, separators=(",", ":"), default=str)
