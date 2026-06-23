import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from noteflow_worker.config import settings


@dataclass(frozen=True)
class NotesGeneration:
    provider: str
    model: str
    heading: str = ""
    section_type: str = "KEY_IDEAS"
    markdown: str = ""
    confidence: float = 0.0
    warnings: list[str] | None = None
    raw_response_json: str | None = None
    error_message: str | None = None


class NotesProvider(Protocol):
    provider_name: str
    model: str

    def generate_sections(self, prompt: str) -> list[NotesGeneration]:
        ...


class DisabledNotesProvider:
    provider_name = "disabled"
    model = "none"

    def generate_sections(self, prompt: str) -> list[NotesGeneration]:
        return [
            NotesGeneration(
                provider=self.provider_name,
                model=self.model,
                error_message="Notes provider is not configured.",
            )
        ]


class GeminiNotesProvider:
    provider_name = "gemini"

    def __init__(self) -> None:
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_notes_model or settings.gemini_vision_model

    def generate_sections(self, prompt: str) -> list[NotesGeneration]:
        if not self.api_key:
            return [NotesGeneration(provider=self.provider_name, model=self.model, error_message="Gemini API key is not configured.")]
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "response_mime_type": "application/json",
                "response_schema": notes_response_schema(),
            },
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + self.model
            + ":generateContent?key="
            + self.api_key
        )
        return generations_with_retries(self.provider_name, self.model, lambda: post_json(url, payload))


class OpenAINotesProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self.api_key = settings.openai_api_key
        self.model = settings.openai_notes_model or settings.openai_vision_model

    def generate_sections(self, prompt: str) -> list[NotesGeneration]:
        if not self.api_key:
            return [NotesGeneration(provider=self.provider_name, model=self.model, error_message="OpenAI API key is not configured.")]
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": "Bearer " + self.api_key}
        return generations_with_retries(
            self.provider_name,
            self.model,
            lambda: post_json("https://api.openai.com/v1/chat/completions", payload, headers=headers),
        )


def notes_response_schema() -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "sections": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "heading": {"type": "STRING"},
                        "sectionType": {"type": "STRING"},
                        "markdown": {"type": "STRING"},
                        "confidence": {"type": "NUMBER"},
                        "warnings": {
                            "type": "ARRAY",
                            "items": {"type": "STRING"},
                        },
                    },
                    "required": ["heading", "sectionType", "markdown", "confidence", "warnings"],
                }
            }
        },
        "required": ["sections"],
    }


def make_notes_provider() -> NotesProvider:
    provider = (settings.notes_provider or "").lower().strip()
    if not provider:
        if settings.gemini_api_key:
            provider = "gemini"
        elif settings.openai_api_key:
            provider = "openai"
        else:
            provider = "disabled"
    if provider == "gemini":
        return GeminiNotesProvider()
    if provider == "openai":
        return OpenAINotesProvider()
    return DisabledNotesProvider()


def generations_with_retries(provider: str, model: str, request_fn) -> list[NotesGeneration]:
    last_error = ""
    for attempt in range(1, max(1, settings.notes_request_max_attempts) + 1):
        try:
            response = request_fn()
            parsed = parse_provider_response(response)
            return generations_from_dict(provider, model, parsed, response)
        except Exception as exc:
            last_error = str(exc)[:2000]
            if attempt >= settings.notes_request_max_attempts or not is_retryable_error(last_error):
                break
            time.sleep(settings.notes_retry_backoff_seconds * attempt)
    return [NotesGeneration(provider=provider, model=model, error_message=last_error or "Notes generation failed.")]


def post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.notes_request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notes API HTTP {exc.code}: {body[:1000]}") from exc


def parse_provider_response(response: dict) -> dict:
    if "candidates" in response:
        text = response["candidates"][0]["content"]["parts"][0].get("text", "")
    elif "choices" in response:
        text = response["choices"][0]["message"].get("content", "")
    else:
        text = json.dumps(response)
    return parse_json_object(text)


def parse_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as first_error:
        try:
            return json.loads(escape_invalid_json_backslashes(stripped))
        except json.JSONDecodeError:
            pass
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            fragment = stripped[start : end + 1]
            try:
                return json.loads(fragment)
            except json.JSONDecodeError:
                return json.loads(escape_invalid_json_backslashes(fragment))
        raise first_error


def escape_invalid_json_backslashes(text: str) -> str:
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)


def generations_from_dict(provider: str, model: str, parsed: dict, raw_response: dict) -> list[NotesGeneration]:
    if not isinstance(parsed, dict):
        parsed = {}

    sections_list = parsed.get("sections")
    if not isinstance(sections_list, list):
        if "heading" in parsed or "markdown" in parsed:
            sections_list = [parsed]
        else:
            sections_list = []

    generations = []
    for sec in sections_list:
        if not isinstance(sec, dict):
            continue
        warnings = sec.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
        generations.append(
            NotesGeneration(
                provider=provider,
                model=model,
                heading=str(sec.get("heading", "")),
                section_type=str(sec.get("sectionType", "KEY_IDEAS")),
                markdown=str(sec.get("markdown", "")),
                confidence=float(sec.get("confidence") or 0.0),
                warnings=[str(item) for item in warnings],
                raw_response_json=json.dumps(redact_raw_response(raw_response), separators=(",", ":")),
            )
        )
    if not generations:
        generations.append(
            NotesGeneration(
                provider=provider,
                model=model,
                heading="Study Notes Section",
                section_type="KEY_IDEAS",
                markdown="",
                confidence=0.5,
                warnings=["no_sections_generated"],
                raw_response_json=json.dumps(redact_raw_response(raw_response), separators=(",", ":")),
            )
        )
    return generations


def redact_raw_response(response: dict) -> dict:
    return {
        "providerResponseKeys": sorted(response.keys()),
        "usageMetadata": response.get("usageMetadata") or response.get("usage"),
    }


def is_retryable_error(error: str) -> bool:
    lowered = error.lower()
    return any(item in lowered for item in ["timeout", "temporar", "429", "500", "502", "503", "504", "connection"])
