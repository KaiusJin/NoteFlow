import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from noteflow_worker.config import settings
from noteflow_worker.db.repository import VisualRegion, VlmResult


VISION_PROMPT = """You are analyzing a cropped region from an academic PDF for a RAG system.

Return ONLY valid JSON with these string fields:
- transcription: exact visible text, handwriting, code, or labels. Use [unclear] for unreadable parts.
- description: concise explanation of diagrams, charts, screenshots, arrows, objects, and relationships.
- latex: LaTeX for any mathematical formulas. Empty string if none.
- code: code transcription if the region contains code. Empty string if none.
- uncertainty: what may be wrong or incomplete.
- search_text: a retrieval-optimized combination of the important terms, transcription, visual meaning, and nearby context.

Preserve code indentation when possible. For handwritten notes, transcribe faithfully and describe layout/relationships."""


@dataclass(frozen=True)
class VisionAnalysis:
    provider: str
    model: str
    transcription: str = ""
    description: str = ""
    latex: str = ""
    code: str = ""
    uncertainty: str = ""
    search_text: str = ""
    raw_response_json: str | None = None
    error_message: str | None = None


class VisionProvider(Protocol):
    provider_name: str
    model: str

    def analyze(self, image_path: str, region: VisualRegion) -> VisionAnalysis:
        ...


class DisabledVisionProvider:
    provider_name = "disabled"
    model = "none"

    def analyze(self, image_path: str, region: VisualRegion) -> VisionAnalysis:
        return VisionAnalysis(
            provider=self.provider_name,
            model=self.model,
            uncertainty="Vision provider is disabled.",
            error_message="Vision provider is disabled.",
        )


class GeminiVisionProvider:
    provider_name = "gemini"

    def __init__(self) -> None:
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_vision_model or "gemini-1.5-flash"

    def analyze(self, image_path: str, region: VisualRegion) -> VisionAnalysis:
        if not self.api_key:
            return VisionAnalysis(
                provider=self.provider_name,
                model=self.model,
                uncertainty="Gemini API key is not configured.",
                error_message="Gemini API key is not configured.",
            )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_for_region(region)},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": encode_image(image_path),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            },
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + self.model
            + ":generateContent?key="
            + self.api_key
        )
        try:
            response = post_json(url, payload)
            text = response["candidates"][0]["content"]["parts"][0].get("text", "")
            parsed = parse_json_object(text)
            return analysis_from_dict(self.provider_name, self.model, parsed, response)
        except Exception as exc:
            return VisionAnalysis(
                provider=self.provider_name,
                model=self.model,
                uncertainty="Gemini vision call failed.",
                error_message=str(exc)[:2000],
            )


class OpenAIVisionProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self.api_key = settings.openai_api_key
        self.model = settings.openai_vision_model or "gpt-4o-mini"

    def analyze(self, image_path: str, region: VisualRegion) -> VisionAnalysis:
        if not self.api_key:
            return VisionAnalysis(
                provider=self.provider_name,
                model=self.model,
                uncertainty="OpenAI API key is not configured.",
                error_message="OpenAI API key is not configured.",
            )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_for_region(region)},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64," + encode_image(image_path)
                            },
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": "Bearer " + self.api_key}
        try:
            response = post_json("https://api.openai.com/v1/chat/completions", payload, headers=headers)
            text = response["choices"][0]["message"].get("content", "")
            parsed = parse_json_object(text)
            return analysis_from_dict(self.provider_name, self.model, parsed, response)
        except Exception as exc:
            return VisionAnalysis(
                provider=self.provider_name,
                model=self.model,
                uncertainty="OpenAI vision call failed.",
                error_message=str(exc)[:2000],
            )


def make_vision_provider() -> VisionProvider:
    provider = (settings.vision_provider or "disabled").lower().strip()
    if provider == "gemini":
        return GeminiVisionProvider()
    if provider == "openai":
        return OpenAIVisionProvider()
    return DisabledVisionProvider()


def prompt_for_region(region: VisualRegion) -> str:
    return (
        VISION_PROMPT
        + "\n\nRegion metadata:\n"
        + json.dumps(
            {
                "page_number": region.page_number,
                "region_index": region.region_index,
                "region_type": region.region_type,
                "bbox": region.bbox_json,
                "metadata": parse_json_object(region.metadata_json or "{}"),
            },
            ensure_ascii=True,
        )
    )


def encode_image(image_path: str) -> str:
    return base64.b64encode(Path(image_path).read_bytes()).decode("ascii")


def post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.vision_request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vision API HTTP {exc.code}: {body[:1000]}") from exc


def parse_json_object(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.removeprefix("json").strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def analysis_from_dict(provider: str, model: str, parsed: dict, raw_response: dict) -> VisionAnalysis:
    return VisionAnalysis(
        provider=provider,
        model=model,
        transcription=str(parsed.get("transcription", "")),
        description=str(parsed.get("description", "")),
        latex=str(parsed.get("latex", "")),
        code=str(parsed.get("code", "")),
        uncertainty=str(parsed.get("uncertainty", "")),
        search_text=str(parsed.get("search_text", "")),
        raw_response_json=json.dumps(redact_raw_response(raw_response), separators=(",", ":")),
    )


def redact_raw_response(response: dict) -> dict:
    return {
        "providerResponseKeys": sorted(response.keys()),
        "usageMetadata": response.get("usageMetadata") or response.get("usage"),
    }
