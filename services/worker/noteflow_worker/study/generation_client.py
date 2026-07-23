"""Typed Agent adapter for the shared Study generation domain services.

The structured Study UI and the Agent both enter through the Spring API's
QuizGenerationService / FlashcardGenerationService. The Agent no longer writes
artifact and task rows itself, which keeps versioning, idempotency and
persistence rules in one place.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from noteflow_worker.config import settings


class StudyGenerationClient:
    def create_targeted_quiz(self, arguments: dict) -> dict:
        return self._post("/internal/study/quiz-generations", quiz_request(arguments))

    def create_flashcards_from_context(self, arguments: dict) -> dict:
        return self._post("/internal/study/flashcard-generations", flashcard_request(arguments))

    def _post(self, path: str, payload: dict) -> dict:
        request = Request(
            settings.noteflow_api_url.rstrip("/") + path,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.noteflow_api_timeout_seconds) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise ValueError(f"Study generation was rejected: {detail}") from error
        except URLError as error:
            raise RuntimeError("The local NoteFlow API is unavailable for study generation.") from error
        if not isinstance(decoded, dict):
            raise RuntimeError("The local NoteFlow API returned an invalid study-generation response.")
        return decoded


def common_request(arguments: dict) -> dict:
    raw_ids = arguments.get("documentIds")
    if not isinstance(raw_ids, list) or not raw_ids:
        raw_ids = [arguments.get("documentId")]
    document_ids = list(dict.fromkeys(str(value).strip() for value in raw_ids if str(value or "").strip()))
    if not document_ids:
        raise ValueError("documentIds must select at least one document.")
    result = {
        "documentIds": document_ids,
        "sourceChunkIds": [str(value).strip() for value in arguments.get("chunkIds", []) if str(value or "").strip()],
        "section": clean(arguments.get("section")),
        "focus": clean(arguments.get("focus")),
        "title": clean(arguments.get("title")),
        "origin": "AGENT",
    }
    return result


def quiz_request(arguments: dict) -> dict:
    result = common_request(arguments)
    result.update({
        "easy": arguments.get("easy"),
        "medium": arguments.get("medium"),
        "hard": arguments.get("hard"),
        "questionTypes": arguments.get("questionTypes"),
        "includeExplanations": arguments.get("includeExplanations"),
    })
    return result


def flashcard_request(arguments: dict) -> dict:
    result = common_request(arguments)
    result.update({
        "count": arguments.get("count"),
        "groupBySection": arguments.get("groupBySection"),
    })
    return result


def clean(value) -> str | None:
    text = str(value or "").strip()
    return text or None
