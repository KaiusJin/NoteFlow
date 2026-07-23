"""Extended typed tool catalog for NoteFlow's local study agent.

Handlers in this module are deliberately thin adapters over existing domain
data and APIs. Read tools query the local workspace. Write tools use the local
Spring API so artifact ownership, validation and persistence remain centralized.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from noteflow_worker.config import settings
from noteflow_worker.conversation.retrieval import Evidence
from noteflow_worker.study.generation_client import StudyGenerationClient


@dataclass(frozen=True)
class ToolkitResult:
    ok: bool
    observation: str
    evidence: list[Evidence] = field(default_factory=list)
    handle: dict | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolkitDefinition:
    name: str
    description: str
    args_schema: dict
    handler: Callable[[dict, object], ToolkitResult]
    kind: Literal["sync", "async"] = "sync"


STRING = {"type": "STRING"}
INTEGER = {"type": "INTEGER"}
NUMBER = {"type": "NUMBER"}
BOOLEAN = {"type": "BOOLEAN"}
STRINGS = {"type": "ARRAY", "items": STRING}


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "OBJECT", "properties": properties, "required": required or []}


def extended_tool_definitions() -> list[ToolkitDefinition]:
    """The six-category public tool catalog, excluding core source tools.

    `search_sources`, `generate_quiz`, and `generate_flashcards` are registered
    in agent.py because they reuse the live embedding state or shared Study
    generation client directly.
    """
    return [
        # Retrieval
        ToolkitDefinition("search_notes", "Search editable local Markdown notes by title and content.",
                          obj({"query": STRING, "limit": INTEGER}, ["query"]), search_notes),
        ToolkitDefinition("search_quiz_history", "Search prior quiz sets and attempts, including scores and status.",
                          obj({"query": STRING, "documentIds": STRINGS, "limit": INTEGER}), search_quiz_history),
        ToolkitDefinition("search_flashcards", "Search existing flashcards by topic, front, back, or deck title.",
                          obj({"query": STRING, "documentIds": STRINGS, "limit": INTEGER}, ["query"]), search_flashcards),
        ToolkitDefinition("retrieve_related_chunks", "Expand context around known source chunks using adjacent chunks.",
                          obj({"chunkIds": STRINGS, "window": INTEGER}, ["chunkIds"]), retrieve_related_chunks),
        ToolkitDefinition("retrieve_previous_conversation", "Recall matching prior conversation turns and long-term memories.",
                          obj({"query": STRING, "limit": INTEGER}, ["query"]), retrieve_previous_conversation),

        # Learning artifacts
        ToolkitDefinition("generate_ai_notes", "Start persistent whole-document AI Notes generation.",
                          obj({"documentId": STRING}, ["documentId"]), generate_ai_notes, "async"),
        ToolkitDefinition("generate_summary", "Save a source-grounded summary as a persistent AI Note artifact.",
                          generated_note_schema(), generate_note_artifact("SUMMARY")),
        ToolkitDefinition("generate_study_guide", "Save a structured study guide as a persistent AI Note artifact.",
                          generated_note_schema(), generate_note_artifact("STUDY_GUIDE")),
        ToolkitDefinition("generate_examples", "Save source-grounded worked examples as a persistent AI Note artifact.",
                          generated_note_schema(), generate_note_artifact("EXAMPLES")),
        ToolkitDefinition("generate_practice_questions", "Create a persistent short-answer practice quiz from context.",
                          obj({"documentIds": STRINGS, "chunkIds": STRINGS, "section": STRING, "focus": STRING,
                               "title": STRING, "count": INTEGER}, ["documentIds"]), generate_practice_questions, "async"),

        # Workspace
        ToolkitDefinition("read_markdown", "Read Markdown from a Library note or parsed document.",
                          obj({"noteId": STRING, "documentId": STRING}), read_markdown),
        ToolkitDefinition("edit_markdown", "Replace exact text in a note with optimistic hash protection.",
                          obj({"noteId": STRING, "findText": STRING, "replacement": STRING,
                               "expectedMarkdownHash": STRING, "replaceAll": BOOLEAN}, ["noteId", "findText", "replacement"]), edit_markdown),
        ToolkitDefinition("insert_section", "Insert a Markdown section before/after a heading or at the end.",
                          obj({"noteId": STRING, "heading": STRING, "sectionMarkdown": STRING,
                               "position": {"type": "STRING", "enum": ["BEFORE", "AFTER", "END"]}},
                              ["noteId", "sectionMarkdown", "position"]), insert_section),
        ToolkitDefinition("delete_section", "Delete one Markdown heading section; requires explicit confirmation.",
                          obj({"noteId": STRING, "heading": STRING, "confirm": BOOLEAN}, ["noteId", "heading", "confirm"]), delete_section),
        ToolkitDefinition("rewrite_paragraph", "Replace one exact Markdown paragraph with a rewritten paragraph.",
                          obj({"noteId": STRING, "originalParagraph": STRING, "rewrittenParagraph": STRING,
                               "expectedMarkdownHash": STRING}, ["noteId", "originalParagraph", "rewrittenParagraph"]), rewrite_paragraph),
        ToolkitDefinition("update_note", "Update a note title and/or complete Markdown body.",
                          obj({"noteId": STRING, "title": STRING, "markdown": STRING}, ["noteId"]), update_note),
        ToolkitDefinition("save_artifact", "Save Markdown as a durable workspace artifact in the Notes section.",
                          obj({"title": STRING, "markdown": STRING, "artifactType": STRING, "folderId": STRING},
                              ["title", "markdown", "artifactType"]), save_artifact),

        # Learning analytics
        ToolkitDefinition("analyze_quiz_performance", "Aggregate quiz accuracy, score, attempts, and topic performance.",
                          analytics_schema(), analyze_quiz_performance),
        ToolkitDefinition("find_weak_topics", "Rank topics with the lowest historical quiz performance.",
                          analytics_schema(), find_weak_topics),
        ToolkitDefinition("estimate_mastery", "Estimate topic mastery from quiz correctness and flashcard review state.",
                          analytics_schema(), estimate_mastery),
        ToolkitDefinition("recommend_review_order", "Recommend a review sequence using weakness, due cards, and recency.",
                          analytics_schema(), recommend_review_order),
        ToolkitDefinition("detect_frequently_wrong_concepts", "Find concepts repeatedly answered incorrectly across attempts.",
                          analytics_schema(), detect_frequently_wrong_concepts),
        ToolkitDefinition("get_learning_profile", "Read the compact, incrementally maintained learning profile for planning.",
                          analytics_schema(), get_learning_profile),
        ToolkitDefinition("get_weak_topics", "Read weak topics with deterministic mastery evidence and repeated mistakes.",
                          analytics_schema(), get_weak_topics),
        ToolkitDefinition("get_due_reviews", "Read topics whose deterministic review schedule is due.",
                          analytics_schema(), get_due_reviews),
        ToolkitDefinition("record_learning_feedback", "Record explicit user feedback about confusion or mastery. Never infer this from one casual message.",
                          obj({"eventId": STRING, "topic": STRING,
                               "feedback": {"type": "STRING", "enum": ["CONFUSED", "MASTERED", "TOO_EASY", "TOO_HARD"]},
                               "documentId": STRING, "mistakeType": STRING, "detail": STRING},
                              ["topic", "feedback"]), record_learning_feedback),
        ToolkitDefinition("get_learning_goals", "Read active structured learning goals, deadlines, priority topics, and document scope.",
                          obj({"includeCompleted": BOOLEAN}), get_learning_goals),
        ToolkitDefinition("set_learning_goal", "Create or update an explicit learning goal.",
                          obj({"goalId": STRING,"title": STRING,"description": STRING,"deadline": STRING,
                               "priority": INTEGER,"topics": STRINGS,"documentIds": STRINGS},["title"]), set_learning_goal),
        ToolkitDefinition("get_learning_preferences", "Read explicit and sufficiently supported behavioral learning preferences.",
                          obj({}), get_learning_preferences),
        ToolkitDefinition("set_learning_preference", "Store an explicit user learning preference.",
                          obj({"key": STRING,"value": STRING},["key","value"]), set_learning_preference),
        ToolkitDefinition("find_learning_artifacts", "Find existing notes, quizzes, or flashcards linked to a topic before generating duplicates.",
                          obj({"topic": STRING,"limit": INTEGER},["topic"]), find_learning_artifacts),
        ToolkitDefinition("link_learning_artifact", "Link a persisted artifact to a topic in Artifact Memory.",
                          obj({"topic": STRING,"artifactType": STRING,"artifactId": STRING,"title": STRING,"documentId": STRING},
                              ["topic","artifactType","artifactId"]), link_learning_artifact),
        ToolkitDefinition("build_dynamic_study_plan", "Build and persist a deterministic plan from goals, weak topics, due reviews, preferences, and existing artifacts.",
                          obj({"title": STRING,"minutes": INTEGER}), build_dynamic_study_plan),
        ToolkitDefinition("get_topic_graph", "Retrieve prerequisite, related, or confusion edges around a topic.",
                          obj({"topic": STRING,"depth": INTEGER},["topic"]), get_topic_graph),
        ToolkitDefinition("get_mastery_trend", "Read versioned mastery history for calibration and explanations.",
                          obj({"topic": STRING,"limit": INTEGER},["topic"]), get_mastery_trend),
        ToolkitDefinition("correct_learning_memory", "Apply an explicit optimistic-lock correction or expiration to topic memory.",
                          obj({"topic": STRING,"scopeId": STRING,"mastery": NUMBER,"active": BOOLEAN,"reason": STRING,"expectedVersion": INTEGER,"confirm": BOOLEAN},
                              ["topic","reason","expectedVersion","confirm"]), correct_learning_memory),

        # Planning
        ToolkitDefinition("create_study_plan", "Persist a Markdown study plan in the Notes section.",
                          obj({"title": STRING, "planMarkdown": STRING, "estimatedMinutes": INTEGER},
                              ["title", "planMarkdown"]), create_study_plan),
        ToolkitDefinition("break_down_task", "Convert a learning goal into explicit ordered tasks.",
                          obj({"goal": STRING, "tasks": {"type": "ARRAY", "items": obj({"title": STRING, "doneWhen": STRING}, ["title", "doneWhen"])}},
                              ["goal", "tasks"]), break_down_task),
        ToolkitDefinition("prioritize_tasks", "Order tasks by urgency, learning impact, and effort.",
                          obj({"tasks": {"type": "ARRAY", "items": obj({"title": STRING, "urgency": NUMBER,
                               "impact": NUMBER, "effortMinutes": INTEGER},
                              ["title", "urgency", "impact", "effortMinutes"])}}, ["tasks"]), prioritize_tasks),
        ToolkitDefinition("decide_next_action", "Choose the next action from candidates using explicit reasons and blockers.",
                          obj({"candidates": STRINGS, "recommendedAction": STRING, "reason": STRING,
                               "blockers": STRINGS}, ["candidates", "recommendedAction", "reason"]), decide_next_action),
        ToolkitDefinition("select_documents", "Select relevant READY documents by title and chunk-text signals.",
                          obj({"query": STRING, "limit": INTEGER}, ["query"]), select_documents),
        ToolkitDefinition("estimate_time", "Estimate total study time from task-level minute estimates.",
                          obj({"tasks": {"type": "ARRAY", "items": obj({"title": STRING, "minutes": INTEGER}, ["title", "minutes"])},
                               "bufferPercent": NUMBER}, ["tasks"]), estimate_time),

        # Validation
        ToolkitDefinition("verify_citation", "Verify chunk citations exist and optional quoted text is grounded.",
                          obj({"chunkIds": STRINGS, "quotedText": STRING}, ["chunkIds"]), verify_citation),
        ToolkitDefinition("check_coverage", "Check whether required topics appear in artifact Markdown.",
                          obj({"markdown": STRING, "requiredTopics": STRINGS}, ["markdown", "requiredTopics"]), check_coverage),
        ToolkitDefinition("detect_hallucination", "Flag claims whose cited chunks have insufficient lexical support.",
                          obj({"claims": {"type": "ARRAY", "items": obj({"claim": STRING, "chunkIds": STRINGS}, ["claim", "chunkIds"])}},
                              ["claims"]), detect_hallucination),
        ToolkitDefinition("evaluate_generated_quiz", "Evaluate a persisted quiz for coverage, grounding, confidence, and duplicates.",
                          obj({"quizSetId": STRING}, ["quizSetId"]), evaluate_generated_quiz),
        ToolkitDefinition("retry_generation", "Retry a PARTIAL or FAILED persisted quiz/deck using its original scope and options.",
                          obj({"artifactType": {"type": "STRING", "enum": ["QUIZ", "FLASHCARDS"]}, "artifactId": STRING},
                              ["artifactType", "artifactId"]), retry_generation, "async"),
    ]


def generated_note_schema() -> dict:
    return obj({"title": STRING, "markdown": STRING, "documentIds": STRINGS,
                "sourceChunkIds": STRINGS, "instructions": STRING}, ["title", "markdown", "documentIds"])


def analytics_schema() -> dict:
    return obj({"documentIds": STRINGS, "quizSetId": STRING, "limit": INTEGER})


def _limit(args: dict, default: int = 10, maximum: int = 100) -> int:
    value = args.get("limit", default)
    return max(1, min(maximum, int(value) if not isinstance(value, bool) else default))


def _api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    request = Request(
        settings.noteflow_api_url.rstrip("/") + path,
        data=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=settings.noteflow_api_timeout_seconds) as response:
            raw = response.read().decode()
    except HTTPError as error:
        raise ValueError(error.read().decode(errors="replace")[:1000]) from error
    except URLError as error:
        raise RuntimeError("The local NoteFlow API is unavailable.") from error
    parsed = json.loads(raw) if raw else {}
    return parsed if isinstance(parsed, dict) else {"items": parsed}


def _rows(state, query: str, params: tuple = ()) -> list[dict]:
    with state.store.connect() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def _one(state, query: str, params: tuple = ()) -> dict | None:
    rows = _rows(state, query, params)
    return rows[0] if rows else None


def _json_observation(tool: str, payload) -> str:
    return json.dumps({"tool": tool, "result": payload}, ensure_ascii=False, separators=(",", ":"), default=str)


def search_notes(args: dict, state) -> ToolkitResult:
    query = str(args["query"]).strip()
    rows = _rows(state, """SELECT id,title,LEFT(markdown,1200) markdown,source_kind,updated_at
      FROM notes WHERE user_id=%s AND (title ILIKE %s OR markdown ILIKE %s)
      ORDER BY updated_at DESC LIMIT %s""", (state.user_id, f"%{query}%", f"%{query}%", _limit(args)))
    return ToolkitResult(True, _json_observation("search_notes", rows))


def search_quiz_history(args: dict, state) -> ToolkitResult:
    query = str(args.get("query") or "").strip()
    document_ids = [str(value) for value in args.get("documentIds") or []]
    rows = _rows(state, """SELECT s.id,s.document_id,s.title,s.status,s.origin,s.created_at,
      COUNT(a.id) attempt_count,MAX(a.completed_at) last_attempt_at,
      COALESCE(AVG(CASE WHEN a.max_score>0 THEN a.score/a.max_score END),0) average_score_ratio
      FROM quiz_sets s LEFT JOIN quiz_attempts a ON a.quiz_set_id=s.id
      WHERE s.user_id=%s AND (%s='' OR s.title ILIKE %s)
        AND (cardinality(%s::uuid[])=0 OR s.document_id=ANY(%s::uuid[]))
      GROUP BY s.id ORDER BY s.created_at DESC LIMIT %s""",
      (state.user_id, query, f"%{query}%", document_ids, document_ids, _limit(args)))
    return ToolkitResult(True, _json_observation("search_quiz_history", rows))


def search_flashcards(args: dict, state) -> ToolkitResult:
    query = str(args["query"]).strip()
    document_ids = [str(value) for value in args.get("documentIds") or []]
    rows = _rows(state, """SELECT f.id,f.deck_id,d.document_id,d.title deck_title,f.topic,f.front,f.back,
      f.difficulty,f.source_pages_json FROM flashcards f JOIN flashcard_decks d ON d.id=f.deck_id
      WHERE d.user_id=%s AND (f.topic ILIKE %s OR f.front ILIKE %s OR f.back ILIKE %s OR d.title ILIKE %s)
        AND (cardinality(%s::uuid[])=0 OR d.document_id=ANY(%s::uuid[]))
      ORDER BY f.created_at DESC LIMIT %s""",
      (state.user_id, *(f"%{query}%",) * 4, document_ids, document_ids, _limit(args, 20, 100)))
    return ToolkitResult(True, _json_observation("search_flashcards", rows))


def retrieve_related_chunks(args: dict, state) -> ToolkitResult:
    chunk_ids = [str(value) for value in args["chunkIds"]]
    window = max(0, min(5, int(args.get("window", 1))))
    rows = _rows(state, """WITH selected AS (
      SELECT c.document_id,c.chunk_index FROM document_chunks c JOIN documents d ON d.id=c.document_id
       WHERE c.id=ANY(%s::uuid[]) AND d.user_id=%s)
      SELECT DISTINCT c.id,c.document_id,d.title document_title,c.section_title,c.page_start,c.page_end,c.page_number,c.content,c.chunk_index
      FROM selected s JOIN document_chunks c ON c.document_id=s.document_id AND c.chunk_index BETWEEN s.chunk_index-%s AND s.chunk_index+%s
      JOIN documents d ON d.id=c.document_id ORDER BY c.document_id,c.chunk_index LIMIT 80""",
      (chunk_ids, state.user_id, window, window))
    evidence = [Evidence(i, "PDF", "DOCUMENT_CHUNK", str(row["id"]), str(row["document_id"]),
                         row.get("document_title") or "", row.get("section_title") or "",
                         row.get("page_start") or row.get("page_number"), row.get("page_end") or row.get("page_number"),
                         row.get("content") or "", (row.get("content") or "")[:500], 1.0) for i, row in enumerate(rows)]
    return ToolkitResult(True, _json_observation("retrieve_related_chunks", {"matches": len(evidence)}), evidence)


def retrieve_previous_conversation(args: dict, state) -> ToolkitResult:
    query = str(args["query"]).strip()
    rows = _rows(state, """SELECT m.id,m.conversation_id,m.role,LEFT(m.content_markdown,1200) content,m.created_at
      FROM rag_messages m JOIN rag_conversations c ON c.id=m.conversation_id
      WHERE c.user_id=%s AND m.content_markdown ILIKE %s
      ORDER BY m.created_at DESC LIMIT %s""", (state.user_id, f"%{query}%", _limit(args)))
    memories = _rows(state, """SELECT id,memory_type,LEFT(content,1000) content,confidence,last_accessed_at
      FROM rag_memories WHERE user_id=%s AND status='ACTIVE' AND content ILIKE %s
      ORDER BY confidence DESC,updated_at DESC LIMIT %s""", (state.user_id, f"%{query}%", _limit(args)))
    return ToolkitResult(True, _json_observation("retrieve_previous_conversation", {"messages": rows, "memories": memories}))


def generate_ai_notes(args: dict, state) -> ToolkitResult:
    document_id = str(args["documentId"])
    result = _api(f"/documents/{document_id}/notes", "POST", {})
    handle = {"kind": "note", "documentId": document_id, **result}
    return ToolkitResult(True, _json_observation("generate_ai_notes", handle), handle=handle)


def generate_note_artifact(artifact_type: str):
    def handler(args: dict, state) -> ToolkitResult:
        del state
        metadata = {"artifactType": artifact_type, "documentIds": args["documentIds"],
                    "sourceChunkIds": args.get("sourceChunkIds") or [], "instructions": args.get("instructions") or ""}
        markdown = str(args["markdown"]).rstrip() + "\n\n<!-- noteflow-agent:" + json.dumps(metadata, separators=(",", ":")) + " -->\n"
        saved = _api("/notes", "POST", {"title": args["title"], "markdown": markdown, "sourceKind": "AI_NOTE", "folderId": None})
        handle = {"kind": "note", "artifactType": artifact_type, "noteId": saved.get("id"), "title": saved.get("title")}
        return ToolkitResult(True, _json_observation("generate_" + artifact_type.lower(), handle), handle=handle)
    return handler


def generate_practice_questions(args: dict, state) -> ToolkitResult:
    del state
    count = max(1, min(60, int(args.get("count", 5))))
    request = dict(args)
    request.update({"easy": 0, "medium": count, "hard": 0, "questionTypes": ["SHORT_ANSWER"],
                    "includeExplanations": True})
    handle = StudyGenerationClient().create_targeted_quiz(request)
    return ToolkitResult(True, _json_observation("generate_practice_questions", handle), handle=handle)


def _load_note(state, note_id: str) -> dict:
    row = _one(state, "SELECT id,title,markdown,updated_at FROM notes WHERE id=%s AND user_id=%s", (note_id, state.user_id))
    if not row:
        raise ValueError("Note not found")
    return row


def _hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode()).hexdigest()


def read_markdown(args: dict, state) -> ToolkitResult:
    note_id, document_id = str(args.get("noteId") or "").strip(), str(args.get("documentId") or "").strip()
    if bool(note_id) == bool(document_id):
        raise ValueError("Provide exactly one of noteId or documentId")
    if note_id:
        row = _load_note(state, note_id)
    else:
        row = _one(state, """SELECT m.document_id id,d.title,m.markdown,m.updated_at
          FROM document_markdown_documents m JOIN documents d ON d.id=m.document_id
          WHERE m.document_id=%s AND d.user_id=%s""", (document_id, state.user_id))
        if not row:
            raise ValueError("Document Markdown not found")
    row["markdownHash"] = _hash(row.get("markdown") or "")
    return ToolkitResult(True, _json_observation("read_markdown", row))


def _save_note(note: dict, markdown: str, title: str | None = None) -> dict:
    return _api(f"/notes/{note['id']}", "PUT", {"title": title or note["title"], "markdown": markdown, "move": False})


def _check_hash(note: dict, expected: str | None) -> None:
    if expected and expected != _hash(note.get("markdown") or ""):
        raise ValueError("Markdown changed since it was read; read it again before editing")


def edit_markdown(args: dict, state) -> ToolkitResult:
    note = _load_note(state, str(args["noteId"]))
    _check_hash(note, args.get("expectedMarkdownHash"))
    markdown, needle = note.get("markdown") or "", str(args["findText"])
    if needle not in markdown:
        raise ValueError("findText was not found exactly")
    updated = markdown.replace(needle, str(args["replacement"]), -1 if args.get("replaceAll") else 1)
    saved = _save_note(note, updated)
    return ToolkitResult(True, _json_observation("edit_markdown", {"noteId": saved.get("id"), "markdownHash": _hash(updated)}))


def _heading_span(markdown: str, heading: str) -> tuple[int, int]:
    pattern = re.compile(rf"(?m)^(?P<marks>#{{1,6}})\s+{re.escape(heading.strip())}\s*$")
    match = pattern.search(markdown)
    if not match:
        raise ValueError("Heading not found")
    next_heading = re.compile(rf"(?m)^#{{1,{len(match.group('marks'))}}}\s+").search(markdown, match.end())
    return match.start(), next_heading.start() if next_heading else len(markdown)


def insert_section(args: dict, state) -> ToolkitResult:
    note = _load_note(state, str(args["noteId"]))
    markdown, section, position = note.get("markdown") or "", str(args["sectionMarkdown"]).strip(), args["position"]
    if position == "END":
        updated = markdown.rstrip() + "\n\n" + section + "\n"
    else:
        start, end = _heading_span(markdown, str(args.get("heading") or ""))
        offset = start if position == "BEFORE" else end
        updated = markdown[:offset].rstrip() + "\n\n" + section + "\n\n" + markdown[offset:].lstrip()
    saved = _save_note(note, updated)
    return ToolkitResult(True, _json_observation("insert_section", {"noteId": saved.get("id"), "markdownHash": _hash(updated)}))


def delete_section(args: dict, state) -> ToolkitResult:
    note = _load_note(state, str(args["noteId"]))
    start, end = _heading_span(note.get("markdown") or "", str(args["heading"]))
    if args.get("confirm") is not True:
        preview = (note.get("markdown") or "")[start:end][:1200]
        return ToolkitResult(False, _json_observation("delete_section", {"confirmationRequired": True, "preview": preview}), error="confirmation_required")
    updated = ((note.get("markdown") or "")[:start].rstrip() + "\n\n" + (note.get("markdown") or "")[end:].lstrip()).strip() + "\n"
    saved = _save_note(note, updated)
    return ToolkitResult(True, _json_observation("delete_section", {"noteId": saved.get("id"), "deletedHeading": args["heading"]}))


def rewrite_paragraph(args: dict, state) -> ToolkitResult:
    forwarded = {"noteId": args["noteId"], "findText": args["originalParagraph"],
                 "replacement": args["rewrittenParagraph"], "expectedMarkdownHash": args.get("expectedMarkdownHash"),
                 "replaceAll": False}
    return edit_markdown(forwarded, state)


def update_note(args: dict, state) -> ToolkitResult:
    note = _load_note(state, str(args["noteId"]))
    if "title" not in args and "markdown" not in args:
        raise ValueError("Provide title and/or markdown")
    saved = _save_note(note, str(args.get("markdown", note.get("markdown") or "")), str(args.get("title") or note["title"]))
    return ToolkitResult(True, _json_observation("update_note", {"noteId": saved.get("id"), "title": saved.get("title")}))


def save_artifact(args: dict, state) -> ToolkitResult:
    del state
    saved = _api("/notes", "POST", {"title": args["title"], "markdown": args["markdown"],
                                     "sourceKind": "AI_NOTE", "folderId": args.get("folderId")})
    handle = {"kind": "note", "artifactType": args["artifactType"], "noteId": saved.get("id"), "title": saved.get("title")}
    return ToolkitResult(True, _json_observation("save_artifact", handle), handle=handle)


def _analytics_rows(args: dict, state) -> list[dict]:
    document_ids = [str(value) for value in args.get("documentIds") or []]
    quiz_set_id = str(args.get("quizSetId") or "")
    return _rows(state, """SELECT q.topic,COUNT(ans.id) answer_count,
      COUNT(ans.id) FILTER (WHERE ans.is_correct=FALSE) wrong_count,
      COALESCE(AVG(CASE WHEN q.points>0 THEN ans.awarded_points/q.points END),0) score_ratio,
      MAX(a.completed_at) last_attempt_at
      FROM quiz_answers ans JOIN quiz_questions q ON q.id=ans.question_id
      JOIN quiz_attempts a ON a.id=ans.attempt_id JOIN quiz_sets s ON s.id=a.quiz_set_id
      WHERE a.user_id=%s AND a.status='COMPLETED' AND (%s='' OR s.id=%s::uuid)
        AND (cardinality(%s::uuid[])=0 OR s.document_id=ANY(%s::uuid[]))
      GROUP BY q.topic ORDER BY score_ratio,wrong_count DESC LIMIT %s""",
      (state.user_id, quiz_set_id, quiz_set_id or None, document_ids, document_ids, _limit(args, 25, 100)))


def analyze_quiz_performance(args: dict, state) -> ToolkitResult:
    rows = _analytics_rows(args, state)
    answers = sum(int(row["answer_count"]) for row in rows)
    weighted = sum(float(row["score_ratio"]) * int(row["answer_count"]) for row in rows)
    payload = {"answerCount": answers, "overallScoreRatio": weighted / answers if answers else 0, "topics": rows}
    return ToolkitResult(True, _json_observation("analyze_quiz_performance", payload))


def find_weak_topics(args: dict, state) -> ToolkitResult:
    rows = _analytics_rows(args, state)
    weak = [{**row, "weakness": round(1 - float(row["score_ratio"]), 4)} for row in rows if float(row["score_ratio"]) < .75]
    return ToolkitResult(True, _json_observation("find_weak_topics", weak))


def estimate_mastery(args: dict, state) -> ToolkitResult:
    quiz = {str(row["topic"]): row for row in _analytics_rows(args, state)}
    cards = _rows(state, """SELECT f.topic,COUNT(*) reviewed,
      AVG(LEAST(1.0,s.repetitions/5.0)) review_ratio FROM flashcard_review_states s
      JOIN flashcards f ON f.id=s.flashcard_id WHERE s.user_id=%s GROUP BY f.topic""", (state.user_id,))
    card_map = {str(row["topic"]): row for row in cards}
    topics = sorted(set(quiz) | set(card_map))
    mastery = []
    for topic in topics:
        quiz_ratio = float(quiz.get(topic, {}).get("score_ratio", 0))
        review_ratio = float(card_map.get(topic, {}).get("review_ratio", 0))
        evidence_sources = int(topic in quiz) + int(topic in card_map)
        score = quiz_ratio if evidence_sources == 1 and topic in quiz else review_ratio if evidence_sources == 1 else .7 * quiz_ratio + .3 * review_ratio
        mastery.append({"topic": topic, "mastery": round(score, 4), "quizRatio": quiz_ratio, "reviewRatio": review_ratio})
    return ToolkitResult(True, _json_observation("estimate_mastery", mastery))


def recommend_review_order(args: dict, state) -> ToolkitResult:
    weak = _analytics_rows(args, state)
    due = _rows(state, """SELECT f.topic,COUNT(*) due_cards FROM flashcard_review_states s JOIN flashcards f ON f.id=s.flashcard_id
      WHERE s.user_id=%s AND s.status<>'SUSPENDED' AND s.due_at<=NOW() GROUP BY f.topic""", (state.user_id,))
    due_map = {str(row["topic"]): int(row["due_cards"]) for row in due}
    result = []
    for row in weak:
        topic = str(row["topic"])
        priority = (1 - float(row["score_ratio"])) * 100 + min(30, due_map.get(topic, 0) * 3)
        result.append({"topic": topic, "priority": round(priority, 2), "dueCards": due_map.get(topic, 0), "scoreRatio": row["score_ratio"]})
    result.sort(key=lambda item: item["priority"], reverse=True)
    return ToolkitResult(True, _json_observation("recommend_review_order", result))


def detect_frequently_wrong_concepts(args: dict, state) -> ToolkitResult:
    rows = [row for row in _analytics_rows(args, state) if int(row["wrong_count"]) >= 2]
    rows.sort(key=lambda row: (int(row["wrong_count"]), -float(row["score_ratio"])), reverse=True)
    return ToolkitResult(True, _json_observation("detect_frequently_wrong_concepts", rows))


def _memory_scope(args: dict, state, *, extra: str = "", order: str = "mastery ASC", maximum: int = 100) -> list[dict]:
    document_ids = [str(value) for value in args.get("documentIds") or []]
    return _rows(state, f"""SELECT topic_key,MAX(topic) topic,
      SUM(mastery*GREATEST(evidence_weight,.1))/SUM(GREATEST(evidence_weight,.1)) mastery,
      MAX(confidence) confidence,SUM(evidence_weight) evidence_weight,SUM(attempts) attempts,
      SUM(correct_count) correct_count,SUM(incorrect_count) incorrect_count,SUM(hint_count) hint_count,
      AVG(recent_trend) recent_trend,AVG(stability_days) stability_days,AVG(calibration_error) calibration_error,
      SUM(lapse_count) lapse_count,MAX(last_activity_at) last_activity_at,
      MIN(next_review_at) next_review_at,BOOL_OR(needs_review) needs_review
      FROM topic_learning_memory WHERE workspace_id=%s
        AND (cardinality(%s::uuid[])=0 OR scope_id=ANY(%s::uuid[])) {extra}
      GROUP BY topic_key ORDER BY {order} LIMIT %s""",
      (state.user_id, document_ids, document_ids, _limit(args, 25, maximum)))


def get_learning_profile(args: dict, state) -> ToolkitResult:
    topics = _memory_scope(args, state, maximum=100)
    attempts = sum(int(row["attempts"]) for row in topics)
    average = sum(float(row["mastery"]) for row in topics) / len(topics) if topics else 0
    payload = {"topicCount": len(topics), "attemptCount": attempts,
               "averageMastery": round(average, 4), "topics": topics}
    return ToolkitResult(True, _json_observation("get_learning_profile", payload))


def get_weak_topics(args: dict, state) -> ToolkitResult:
    topics = _memory_scope(args, state, extra="AND (mastery<.75 OR needs_review)",
                           order="needs_review DESC,mastery ASC,incorrect_count DESC", maximum=100)
    keys = [str(row["topic_key"]) for row in topics]
    mistakes = _rows(state, """SELECT topic_key,mistake_type,summary,SUM(occurrences) occurrences,MAX(last_seen_at) last_seen_at
      FROM mistake_memory WHERE workspace_id=%s AND topic_key=ANY(%s::text[])
      GROUP BY topic_key,mistake_type,summary ORDER BY occurrences DESC,last_seen_at DESC""", (state.user_id, keys)) if keys else []
    by_topic: dict[str, list[dict]] = {}
    for mistake in mistakes:
        by_topic.setdefault(str(mistake["topic_key"]), []).append(mistake)
    for topic in topics:
        topic["weakness"] = round(1 - float(topic["mastery"]), 4)
        topic["mistakes"] = by_topic.get(str(topic["topic_key"]), [])[:3]
    return ToolkitResult(True, _json_observation("get_weak_topics", topics))


def get_due_reviews(args: dict, state) -> ToolkitResult:
    topics = _memory_scope(args, state,
        extra="AND needs_review AND next_review_at IS NOT NULL AND next_review_at<=NOW()",
        order="next_review_at ASC,mastery ASC", maximum=100)
    return ToolkitResult(True, _json_observation("get_due_reviews", topics))


def record_learning_feedback(args: dict, state) -> ToolkitResult:
    body = {"eventId": str(args.get("eventId") or f"agent-feedback:{uuid4()}"),
            "topic": args["topic"], "feedback": args["feedback"], "documentId": args.get("documentId"),
            "mistakeType": args.get("mistakeType"), "detail": args.get("detail")}
    result = _api("/learning-memory/feedback", "POST", body)
    return ToolkitResult(True, _json_observation("record_learning_feedback", result))


def get_learning_goals(args: dict, state) -> ToolkitResult:
    del state
    result = _api("/learning-memory/goals?all=" + str(bool(args.get("includeCompleted", False))).lower())
    return ToolkitResult(True, _json_observation("get_learning_goals", result))


def set_learning_goal(args: dict, state) -> ToolkitResult:
    del state
    result = _api("/learning-memory/goals", "PUT", {"id": args.get("goalId"), "title": args["title"],
        "description": args.get("description"), "deadline": args.get("deadline"), "priority": args.get("priority"),
        "topics": args.get("topics") or [], "documentIds": args.get("documentIds") or []})
    return ToolkitResult(True, _json_observation("set_learning_goal", result))


def get_learning_preferences(args: dict, state) -> ToolkitResult:
    del args, state
    return ToolkitResult(True, _json_observation("get_learning_preferences", _api("/learning-memory/preferences")))


def set_learning_preference(args: dict, state) -> ToolkitResult:
    del state
    result = _api("/learning-memory/preferences/" + quote(str(args["key"]), safe=""), "PUT",
                  {"value": args["value"], "source": "EXPLICIT", "confidence": 1})
    return ToolkitResult(True, _json_observation("set_learning_preference", result))


def find_learning_artifacts(args: dict, state) -> ToolkitResult:
    del state
    query = urlencode({"topic": args["topic"], "limit": _limit(args, 20, 100)})
    return ToolkitResult(True, _json_observation("find_learning_artifacts", _api("/learning-memory/artifacts?" + query)))


def link_learning_artifact(args: dict, state) -> ToolkitResult:
    del state
    result = _api("/learning-memory/artifacts", "POST", {"topic": args["topic"], "type": args["artifactType"],
        "artifactId": args["artifactId"], "title": args.get("title"), "documentId": args.get("documentId"), "metadata": {}})
    return ToolkitResult(True, _json_observation("link_learning_artifact", result))


def build_dynamic_study_plan(args: dict, state) -> ToolkitResult:
    del state
    result = _api("/learning-memory/study-plans", "POST", {"title": args.get("title"), "minutes": args.get("minutes", 60)})
    return ToolkitResult(True, _json_observation("build_dynamic_study_plan", result), handle={"kind": "study_plan", "planId": result.get("id")})


def get_topic_graph(args: dict, state) -> ToolkitResult:
    del state
    query = urlencode({"topic": args["topic"], "depth": max(1, min(4, int(args.get("depth", 2))))})
    return ToolkitResult(True, _json_observation("get_topic_graph", _api("/learning-memory/topic-graph?" + query)))


def get_mastery_trend(args: dict, state) -> ToolkitResult:
    del state
    topic = quote(str(args["topic"]), safe="")
    return ToolkitResult(True, _json_observation("get_mastery_trend", _api(f"/learning-memory/topics/{topic}/trend?limit={_limit(args,50,500)}")))


def correct_learning_memory(args: dict, state) -> ToolkitResult:
    del state
    if args.get("confirm") is not True:
        raise PermissionError("Learning-memory correction requires explicit confirmation")
    result = _api("/learning-memory/corrections", "POST", {"topic": args["topic"],"scopeId": args.get("scopeId"),
        "mastery": args.get("mastery"),"active": args.get("active"),"reason": args["reason"],
        "expectedVersion": args.get("expectedVersion")})
    return ToolkitResult(True, _json_observation("correct_learning_memory", result))


def create_study_plan(args: dict, state) -> ToolkitResult:
    markdown = str(args["planMarkdown"]).rstrip()
    if args.get("estimatedMinutes") is not None:
        markdown += f"\n\n_Estimated time: {int(args['estimatedMinutes'])} minutes_\n"
    return save_artifact({"title": args["title"], "markdown": markdown, "artifactType": "STUDY_PLAN"}, state)


def break_down_task(args: dict, state) -> ToolkitResult:
    del state
    tasks = [{"order": index + 1, **task} for index, task in enumerate(args["tasks"])]
    return ToolkitResult(True, _json_observation("break_down_task", {"goal": args["goal"], "tasks": tasks}))


def prioritize_tasks(args: dict, state) -> ToolkitResult:
    del state
    ranked = []
    for task in args["tasks"]:
        effort = max(1, int(task["effortMinutes"]))
        score = (float(task["urgency"]) * .45 + float(task["impact"]) * .55) / math.sqrt(effort)
        ranked.append({**task, "priorityScore": round(score, 4)})
    ranked.sort(key=lambda task: task["priorityScore"], reverse=True)
    return ToolkitResult(True, _json_observation("prioritize_tasks", ranked))


def decide_next_action(args: dict, state) -> ToolkitResult:
    del state
    if args["recommendedAction"] not in args["candidates"]:
        raise ValueError("recommendedAction must be one of candidates")
    return ToolkitResult(True, _json_observation("decide_next_action", args))


def select_documents(args: dict, state) -> ToolkitResult:
    query = str(args["query"]).strip()
    rows = _rows(state, """SELECT d.id,d.title,d.document_type,d.page_count,
      COUNT(c.id) FILTER (WHERE c.content ILIKE %s) matching_chunks
      FROM documents d LEFT JOIN document_chunks c ON c.document_id=d.id
      WHERE d.user_id=%s AND d.status='READY' AND (d.title ILIKE %s OR c.content ILIKE %s)
      GROUP BY d.id ORDER BY (d.title ILIKE %s) DESC,matching_chunks DESC,d.created_at DESC LIMIT %s""",
      (f"%{query}%", state.user_id, f"%{query}%", f"%{query}%", f"%{query}%", _limit(args, 8, 25)))
    return ToolkitResult(True, _json_observation("select_documents", rows))


def estimate_time(args: dict, state) -> ToolkitResult:
    del state
    base = sum(max(0, int(task["minutes"])) for task in args["tasks"])
    buffer_percent = max(0, min(100, float(args.get("bufferPercent", 15))))
    total = math.ceil(base * (1 + buffer_percent / 100))
    return ToolkitResult(True, _json_observation("estimate_time", {"baseMinutes": base, "bufferPercent": buffer_percent,
                                                                    "totalMinutes": total, "hours": round(total / 60, 2)}))


def _citation_rows(args: dict, state) -> list[dict]:
    ids = [str(value) for value in args.get("chunkIds") or []]
    if not ids:
        return []
    return _rows(state, """SELECT c.id,c.document_id,c.content,c.page_start,c.page_end
      FROM document_chunks c JOIN documents d ON d.id=c.document_id
      WHERE c.id=ANY(%s::uuid[]) AND d.user_id=%s""", (ids, state.user_id))


def verify_citation(args: dict, state) -> ToolkitResult:
    rows = _citation_rows(args, state)
    requested = {str(value) for value in args["chunkIds"]}
    found = {str(row["id"]) for row in rows}
    quote = str(args.get("quotedText") or "").strip().casefold()
    quote_supported = not quote or any(quote in (row.get("content") or "").casefold() for row in rows)
    payload = {"valid": requested == found and quote_supported, "missingChunkIds": sorted(requested - found),
               "quoteSupported": quote_supported, "sources": [{"chunkId": str(row["id"]), "documentId": str(row["document_id"]),
                                                                  "pages": [row.get("page_start"), row.get("page_end")]} for row in rows]}
    return ToolkitResult(payload["valid"], _json_observation("verify_citation", payload), error=None if payload["valid"] else "citation_invalid")


def _terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[\w'-]{3,}", text.casefold()) if word not in {"the", "and", "for", "with", "that", "this"}}


def check_coverage(args: dict, state) -> ToolkitResult:
    del state
    markdown = str(args["markdown"]).casefold()
    topics = [str(topic) for topic in args["requiredTopics"]]
    covered = [topic for topic in topics if topic.casefold() in markdown]
    payload = {"coverageRatio": len(covered) / len(topics) if topics else 1, "covered": covered,
               "missing": [topic for topic in topics if topic not in covered]}
    return ToolkitResult(not payload["missing"], _json_observation("check_coverage", payload), error=None if not payload["missing"] else "coverage_incomplete")


def detect_hallucination(args: dict, state) -> ToolkitResult:
    results = []
    for item in args["claims"]:
        rows = _citation_rows({"chunkIds": item["chunkIds"]}, state)
        claim_terms = _terms(str(item["claim"]))
        source_terms = _terms(" ".join(row.get("content") or "" for row in rows))
        support = len(claim_terms & source_terms) / max(1, len(claim_terms))
        results.append({"claim": item["claim"], "supportScore": round(support, 4),
                        "flagged": not rows or support < .35, "citationCount": len(rows)})
    flagged = [item for item in results if item["flagged"]]
    return ToolkitResult(not flagged, _json_observation("detect_hallucination", {"claims": results, "flaggedCount": len(flagged)}),
                         error=None if not flagged else "unsupported_claims")


def evaluate_generated_quiz(args: dict, state) -> ToolkitResult:
    quiz_id = str(args["quizSetId"])
    meta = _one(state, "SELECT id,title,status,total_source_groups,completed_source_groups FROM quiz_sets WHERE id=%s AND user_id=%s",
                (quiz_id, state.user_id))
    if not meta:
        raise ValueError("Quiz not found")
    stats = _one(state, """SELECT COUNT(*) question_count,COUNT(DISTINCT topic) topic_count,
      COUNT(*) FILTER (WHERE source_chunk_ids_json='[]') ungrounded_count,
      COUNT(*) FILTER (WHERE confidence<0.5) low_confidence_count,
      COUNT(*)-COUNT(DISTINCT dedupe_hash) duplicate_count FROM quiz_questions WHERE quiz_set_id=%s""", (quiz_id,)) or {}
    complete = int(meta.get("completed_source_groups") or 0) >= int(meta.get("total_source_groups") or 0)
    valid = meta["status"] == "READY" and complete and not any(int(stats.get(key) or 0) for key in ("ungrounded_count", "low_confidence_count", "duplicate_count"))
    payload = {**meta, **stats, "coverageComplete": complete, "valid": valid}
    return ToolkitResult(valid, _json_observation("evaluate_generated_quiz", payload), error=None if valid else "quiz_quality_failed")


def retry_generation(args: dict, state) -> ToolkitResult:
    artifact_type, artifact_id = args["artifactType"], str(args["artifactId"])
    table = "quiz_sets" if artifact_type == "QUIZ" else "flashcard_decks"
    row = _one(state, f"SELECT id,title,status,source_scope_json,generation_options_json FROM {table} WHERE id=%s AND user_id=%s",
               (artifact_id, state.user_id))
    if not row or row["status"] not in {"PARTIAL", "FAILED"}:
        raise ValueError("Artifact is not retryable")
    scope = json.loads(row.get("source_scope_json") or "{}")
    options = json.loads(row.get("generation_options_json") or "{}")
    request = {"documentIds": scope.get("documentIds") or [], "chunkIds": scope.get("chunkIds") or [],
               "section": scope.get("sectionQuery"), "focus": scope.get("focus"), "title": row["title"]}
    if artifact_type == "QUIZ":
        counts = options.get("difficultyCounts") or {}
        request.update({"easy": counts.get("EASY"), "medium": counts.get("MEDIUM"), "hard": counts.get("HARD"),
                        "questionTypes": options.get("questionTypes"),
                        "includeExplanations": options.get("includeExplanations")})
    else:
        request.update({"count": options.get("targetCount"), "groupBySection": options.get("groupBySection")})
    client = StudyGenerationClient()
    handle = client.create_targeted_quiz(request) if artifact_type == "QUIZ" else client.create_flashcards_from_context(request)
    return ToolkitResult(True, _json_observation("retry_generation", handle), handle=handle)
