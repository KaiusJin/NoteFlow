from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
import threading
from dataclasses import dataclass, field
from typing import Protocol

from noteflow_worker.config import settings
from noteflow_worker.notes.providers import convert_gemini_schema_to_json_schema, is_retryable_error, parse_provider_response
from noteflow_worker.study.models import (
    DIFFICULTIES, FLASHCARD_TYPES, QUESTION_TYPES, FlashcardCandidate, GradeResult,
    QuizQuestionCandidate, RubricPoint,
)


FLASHCARD_KEYS = {"cardType", "front", "back", "clozeText", "difficulty", "topic", "hint", "tags",
                  "sourceChunkIndexes", "confidence", "warnings"}
QUESTION_KEYS = {"questionType", "difficulty", "topic", "stem", "options", "correctAnswer", "answerKey",
                 "rubric", "explanation", "relatedFormula", "commonMistake", "distractorRationales", "points",
                 "sourceChunkIndexes", "confidence", "warnings"}
GRADE_KEYS = {"isCorrect", "awardedPoints", "feedback", "keyPointsHit"}
_REQUEST_SEMAPHORE = threading.BoundedSemaphore(max(1, settings.study_global_max_concurrent_requests))


class StudyProvider(Protocol):
    provider_name: str
    model: str

    def generate_flashcards(self, prompt: str) -> list[FlashcardCandidate]: ...
    def generate_questions(self, prompt: str) -> list[QuizQuestionCandidate]: ...
    def grade_answer(self, prompt: str, max_points: float, rubric_count: int) -> GradeResult: ...
    def usage_snapshot(self) -> dict[str, int]: ...


@dataclass
class StructuredStudyProvider:
    provider_name: str
    model: str
    api_key: str = field(repr=False)
    _usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0,
                                                           "totalTokens": 0, "successfulResponses": 0}, init=False)
    _usage_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def generate_flashcards(self, prompt: str) -> list[FlashcardCandidate]:
        return self._request(prompt, flashcards_response_schema(), "noteflow_flashcards", flashcards_from_dict)

    def generate_questions(self, prompt: str) -> list[QuizQuestionCandidate]:
        return self._request(prompt, questions_response_schema(), "noteflow_quiz_questions", questions_from_dict)

    def grade_answer(self, prompt: str, max_points: float, rubric_count: int) -> GradeResult:
        def parse_grade(parsed):
            validate_grade_response(parsed, max_points, rubric_count)
            awarded = float(parsed["awardedPoints"])
            is_correct = awarded / max_points >= settings.quiz_free_text_pass_threshold
            return GradeResult(is_correct, awarded, parsed["feedback"],
                               parsed["keyPointsHit"], "LLM")
        return self._request(prompt, grade_response_schema(), "noteflow_quiz_grade", parse_grade)

    def _request(self, prompt: str, schema: dict, schema_name: str, response_parser):
        if not self.api_key:
            raise RuntimeError(f"{self.provider_name} API key is not configured.")
        if self.provider_name == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {
                "temperature": 0.15, "maxOutputTokens": settings.study_max_output_tokens,
                "response_mime_type": "application/json", "response_schema": schema}}
            headers = {}
        else:
            url = "https://api.openai.com/v1/chat/completions"
            payload = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.15,
                       "max_tokens": settings.study_max_output_tokens,
                       "response_format": {"type": "json_schema", "json_schema": {
                           "name": schema_name, "strict": True,
                           "schema": convert_gemini_schema_to_json_schema(schema)}}}
            headers = {"Authorization": f"Bearer {self.api_key}"}
        return request_with_retries(url, payload, headers, response_parser, self._record_usage)

    def _record_usage(self, response: dict) -> None:
        usage = response.get("usageMetadata") or response.get("usage") or {}
        input_tokens = int(usage.get("promptTokenCount") or usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("candidatesTokenCount") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("totalTokenCount") or usage.get("total_tokens") or input_tokens + output_tokens)
        with self._usage_lock:
            self._usage["inputTokens"] += input_tokens
            self._usage["outputTokens"] += output_tokens
            self._usage["totalTokens"] += total_tokens
            self._usage["successfulResponses"] += 1

    def usage_snapshot(self) -> dict[str, int]:
        with self._usage_lock:
            return dict(self._usage)


def make_study_provider() -> StudyProvider:
    provider = (settings.study_llm_provider or settings.notes_provider or "").lower().strip()
    if not provider:
        provider = "gemini" if settings.gemini_api_key else "openai" if settings.openai_api_key else "disabled"
    if provider == "gemini":
        return StructuredStudyProvider("gemini", settings.study_gemini_model or settings.gemini_notes_model,
                                       settings.gemini_api_key)
    if provider == "openai":
        return StructuredStudyProvider("openai", settings.study_openai_model or settings.openai_notes_model,
                                       settings.openai_api_key)
    raise RuntimeError("Study provider is not configured. Set STUDY_LLM_PROVIDER and an API key.")


def _string_array() -> dict:
    return {"type": "ARRAY", "items": {"type": "STRING"}}


def flashcards_response_schema() -> dict:
    props = {
        "cardType": {"type": "STRING", "enum": sorted(FLASHCARD_TYPES)}, "front": {"type": "STRING"},
        "back": {"type": "STRING"}, "clozeText": {"type": "STRING"},
        "difficulty": {"type": "STRING", "enum": sorted(DIFFICULTIES)}, "topic": {"type": "STRING"},
        "hint": {"type": "STRING"}, "tags": _string_array(),
        "sourceChunkIndexes": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "confidence": {"type": "NUMBER"}, "warnings": _string_array(),
    }
    return {"type": "OBJECT", "properties": {"flashcards": {"type": "ARRAY", "items": {
        "type": "OBJECT", "properties": props, "required": sorted(FLASHCARD_KEYS)}}}, "required": ["flashcards"]}


def questions_response_schema() -> dict:
    rubric = {"type": "OBJECT", "properties": {"point": {"type": "STRING"}, "weight": {"type": "NUMBER"}},
              "required": ["point", "weight"]}
    props = {
        "questionType": {"type": "STRING", "enum": sorted(QUESTION_TYPES)},
        "difficulty": {"type": "STRING", "enum": sorted(DIFFICULTIES)}, "topic": {"type": "STRING"},
        "stem": {"type": "STRING"}, "options": _string_array(), "correctAnswer": {"type": "STRING"},
        "answerKey": {"type": "STRING"}, "rubric": {"type": "ARRAY", "items": rubric},
        "explanation": {"type": "STRING"}, "relatedFormula": {"type": "STRING"},
        "commonMistake": {"type": "STRING"}, "distractorRationales": _string_array(),
        "points": {"type": "NUMBER"},
        "sourceChunkIndexes": {"type": "ARRAY", "items": {"type": "INTEGER"}},
        "confidence": {"type": "NUMBER"}, "warnings": _string_array(),
    }
    return {"type": "OBJECT", "properties": {"questions": {"type": "ARRAY", "items": {
        "type": "OBJECT", "properties": props, "required": sorted(QUESTION_KEYS)}}}, "required": ["questions"]}


def grade_response_schema() -> dict:
    props = {"isCorrect": {"type": "BOOLEAN"}, "awardedPoints": {"type": "NUMBER"},
             "feedback": {"type": "STRING"},
             "keyPointsHit": {"type": "ARRAY", "items": {"type": "BOOLEAN"}}}
    return {"type": "OBJECT", "properties": props, "required": sorted(GRADE_KEYS)}


def _valid_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def flashcards_from_dict(parsed: dict) -> list[FlashcardCandidate]:
    if not isinstance(parsed, dict) or set(parsed) != {"flashcards"} or not isinstance(parsed["flashcards"], list):
        raise ValueError("Flashcard response must contain only a flashcards array.")
    result = []
    for i, card in enumerate(parsed["flashcards"]):
        if not isinstance(card, dict) or set(card) != FLASHCARD_KEYS:
            raise ValueError(f"Flashcard {i} has invalid fields.")
        if card["cardType"] not in FLASHCARD_TYPES or card["difficulty"] not in DIFFICULTIES:
            raise ValueError(f"Flashcard {i} has an unsupported enum value.")
        if not all(_valid_string(card[key]) for key in ("front", "back", "topic")):
            raise ValueError(f"Flashcard {i} has empty required text.")
        _validate_common(card, i, "Flashcard")
        if not isinstance(card["tags"], list) or any(not isinstance(tag, str) for tag in card["tags"]):
            raise ValueError(f"Flashcard {i} tags must be strings.")
        if card["cardType"] == "CLOZE" and not _valid_string(card["clozeText"]):
            raise ValueError(f"Flashcard {i} CLOZE requires clozeText.")
        result.append(FlashcardCandidate(card["cardType"], card["front"], card["back"], card["clozeText"],
                                        card["difficulty"], card["topic"], card["hint"], card["tags"],
                                        card["sourceChunkIndexes"], float(card["confidence"]), card["warnings"]))
    return result


def questions_from_dict(parsed: dict) -> list[QuizQuestionCandidate]:
    if not isinstance(parsed, dict) or set(parsed) != {"questions"} or not isinstance(parsed["questions"], list):
        raise ValueError("Quiz response must contain only a questions array.")
    result = []
    for i, question in enumerate(parsed["questions"]):
        if not isinstance(question, dict) or set(question) != QUESTION_KEYS:
            raise ValueError(f"Question {i} has invalid fields.")
        if question["questionType"] not in QUESTION_TYPES or question["difficulty"] not in DIFFICULTIES:
            raise ValueError(f"Question {i} has an unsupported enum value.")
        if not all(_valid_string(question[key]) for key in ("stem", "topic", "correctAnswer", "answerKey", "explanation")):
            raise ValueError(f"Question {i} has empty required text.")
        _validate_common(question, i, "Question")
        if isinstance(question["points"], bool) or not isinstance(question["points"], (int, float)) or question["points"] <= 0:
            raise ValueError(f"Question {i} points must be positive.")
        if not isinstance(question["rubric"], list) or any(set(point) != {"point", "weight"} for point in question["rubric"]):
            raise ValueError(f"Question {i} rubric is invalid.")
        rubric = [RubricPoint(point["point"], float(point["weight"])) for point in question["rubric"]]
        if not rubric or abs(sum(point.weight for point in rubric) - float(question["points"])) > 0.01:
            raise ValueError(f"Question {i} rubric weights must equal points.")
        if question["questionType"] == "MULTIPLE_CHOICE":
            if len(question["options"]) < 2 or question["correctAnswer"] not in question["options"]:
                raise ValueError(f"Question {i} has invalid multiple-choice options.")
            if len(question["distractorRationales"]) != len(question["options"]) - 1:
                raise ValueError(f"Question {i} must explain every distractor.")
        result.append(QuizQuestionCandidate(question["questionType"], question["difficulty"], question["topic"],
            question["stem"], question["options"], question["correctAnswer"], question["answerKey"], rubric,
            question["explanation"], question["relatedFormula"], question["commonMistake"],
            question["distractorRationales"], float(question["points"]), question["sourceChunkIndexes"],
            float(question["confidence"]), question["warnings"]))
    return result


def _validate_common(item: dict, index: int, name: str) -> None:
    if not isinstance(item["sourceChunkIndexes"], list) or not item["sourceChunkIndexes"] or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in item["sourceChunkIndexes"]):
        raise ValueError(f"{name} {index} must cite source indexes.")
    confidence = item["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError(f"{name} {index} confidence must be between 0 and 1.")
    if not isinstance(item["warnings"], list) or any(not isinstance(v, str) for v in item["warnings"]):
        raise ValueError(f"{name} {index} warnings must be strings.")


def validate_grade_response(parsed: dict, max_points: float, rubric_count: int) -> None:
    if not isinstance(parsed, dict) or set(parsed) != GRADE_KEYS:
        raise ValueError("Grade response has invalid fields.")
    points = parsed["awardedPoints"]
    if isinstance(points, bool) or not isinstance(points, (int, float)) or not 0 <= points <= max_points:
        raise ValueError("Awarded points are outside the allowed range.")
    if not _valid_string(parsed["feedback"]) or not isinstance(parsed["isCorrect"], bool):
        raise ValueError("Grade response has invalid feedback or correctness.")
    if not isinstance(parsed["keyPointsHit"], list) or len(parsed["keyPointsHit"]) != rubric_count or any(
            not isinstance(v, bool) for v in parsed["keyPointsHit"]):
        raise ValueError("Grade response must return one boolean per rubric point.")


def request_with_retries(url: str, payload: dict, headers: dict, response_parser=lambda value: value,
                         response_observer=lambda value: None):
    last: Exception | None = None
    for attempt in range(max(1, settings.study_request_max_attempts)):
        try:
            request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json", **headers}, method="POST")
            with _REQUEST_SEMAPHORE:
                with urllib.request.urlopen(request, timeout=settings.study_request_timeout_seconds) as response:
                    raw = json.loads(response.read().decode())
                    response_observer(raw)
                    parsed = parse_provider_response(raw)
            return response_parser(parsed)
        except urllib.error.HTTPError as exc:
            last = RuntimeError(f"Study API HTTP {exc.code}: {exc.read().decode(errors='replace')[:1000]}")
        except Exception as exc:
            last = exc
        retryable = isinstance(last, (ValueError, json.JSONDecodeError)) or is_retryable_error(str(last))
        if attempt + 1 >= settings.study_request_max_attempts or not retryable:
            break
        delay = settings.study_retry_backoff_seconds * (2 ** attempt) + random.uniform(0, 0.25)
        time.sleep(min(30.0, delay))
    raise RuntimeError(str(last or "Study provider request failed."))
