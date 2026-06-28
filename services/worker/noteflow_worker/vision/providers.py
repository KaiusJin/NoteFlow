import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

from noteflow_worker.config import settings
from noteflow_worker.db.repository import VisualRegion, VlmResult

VISION_KEYS = {
    "transcription", "description", "latex", "code", "uncertainty", "search_text",
    "content_kind", "importance", "reading_order", "language",
}

VISION_PROMPT = """You are analyzing a cropped region from an academic PDF for a high-fidelity RAG corpus.

Return ONLY valid JSON with these string fields:
- transcription: exact visible text, handwriting, code, or labels; never summarize or invent missing text. Use [unclear] for unreadable parts. Preserve headings, lists, table rows, line breaks, and multi-column reading order.
- description: concise explanation of diagrams, charts, screenshots, arrows, objects, and relationships.
- latex: compilable LaTeX for every mathematical formula, preserving aligned/cases/matrix structure. If the crop contains multiple independent display formulas, put the literal separator line ---FORMULA--- between them. Empty string if none.
- code: code transcription if the region contains code. Empty string if none.
- uncertainty: identify exact spans, symbols, reading order, or crops that may be wrong or incomplete.
- search_text: a retrieval-optimized combination of the important terms, transcription, visual meaning, and nearby context.
- content_kind: one of prose, handwriting, formula, code, table, diagram, chart, screenshot, decorative, mixed, unknown.
- importance: one of high, medium, low, based on academic/retrieval value.
- reading_order: concise instructions for the order in which blocks/columns/arrows should be read.
- language: primary visible language code, or mixed/unknown.

Rules:
1. Native-text context in metadata is context only; do not repeat it unless visibly present in the image.
2. Preserve code indentation and do not translate identifiers.
3. For handwritten notes, follow arrows and derivation order and retain corrections/strike-through meaning.
4. For tables, keep row/column relationships explicit in transcription.
5. If the image is only decorative, say so in description and leave transcription empty.
6. Never concatenate independent equations on one line. Use ---FORMULA--- between independent formulas; use an aligned environment with explicit \\\\ row breaks only when the lines are one derivation.
7. For FORMULA_IMAGE crops, transcribe the complete two-dimensional expression into LaTeX, including fraction bars, limits, roots, superscripts, subscripts, cases, and equation numbers; do not repeat nearby prose in latex."""


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
    content_kind: str = "unknown"
    importance: str = "medium"
    reading_order: str = ""
    language: str = "unknown"
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

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.gemini_api_key
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
                "response_schema": vision_response_schema(),
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

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.openai_api_key
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
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "noteflow_visual_analysis",
                    "strict": True,
                    "schema": openai_vision_response_schema(),
                },
            },
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


class McpVisionProvider:
    """HTTP JSON-RPC MCP client for a vendor-neutral vision tool."""

    provider_name = "mcp"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.mcp_vision_api_key
        self.endpoint = settings.mcp_vision_endpoint
        self.tool = settings.mcp_vision_tool
        self.model = self.tool
        self.protocol_version = settings.mcp_protocol_version
        self.session_id: str | None = None
        self._session_lock = Lock()

    def analyze(self, image_path: str, region: VisualRegion) -> VisionAnalysis:
        if not self.endpoint:
            return VisionAnalysis(
                provider=self.provider_name,
                model=self.model,
                error_message="MCP vision endpoint is not configured.",
                uncertainty="MCP vision endpoint is not configured.",
            )
        payload = {
            "jsonrpc": "2.0",
            "id": f"{region.document_id}:{region.page_number}:{region.region_index}",
            "method": "tools/call",
            "params": {
                "name": self.tool,
                "arguments": {
                    "prompt": prompt_for_region(region),
                    "image_base64": encode_image(image_path),
                    "mime_type": "image/png",
                    "response_schema": openai_vision_response_schema(),
                },
            },
        }
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        try:
            response = self._call_tool(payload, headers)
            if response.get("error"):
                raise RuntimeError("MCP tool error: " + json.dumps(response["error"])[:1000])
            result = response.get("result") or {}
            parsed = result.get("structuredContent")
            if not isinstance(parsed, dict):
                content = result.get("content") or []
                text = next(
                    (item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"),
                    "",
                )
                parsed = parse_json_object(text)
            return analysis_from_dict(self.provider_name, self.model, parsed, response)
        except Exception as exc:
            return VisionAnalysis(
                provider=self.provider_name,
                model=self.model,
                error_message=str(exc)[:2000],
                uncertainty="MCP vision call failed.",
            )

    def _call_tool(self, payload: dict, auth_headers: dict[str, str]) -> dict:
        for session_attempt in range(2):
            self._ensure_initialized(auth_headers)
            request_headers = {
                **auth_headers,
                "MCP-Protocol-Version": self.protocol_version,
                **({"Mcp-Session-Id": self.session_id} if self.session_id and self.session_id != "stateless" else {}),
            }
            try:
                response, _ = post_mcp_json(self.endpoint, payload, headers=request_headers)
                return response
            except RuntimeError as exc:
                if session_attempt == 0 and self.session_id != "stateless" and any(
                    marker in str(exc) for marker in ("MCP HTTP 400", "MCP HTTP 404")
                ):
                    with self._session_lock:
                        self.session_id = None
                    continue
                raise
        raise RuntimeError("MCP session could not be re-established.")

    def _ensure_initialized(self, auth_headers: dict[str, str]) -> None:
        if self.session_id is not None:
            return
        with self._session_lock:
            if self.session_id is not None:
                return
            initialize = {
                "jsonrpc": "2.0",
                "id": "noteflow-initialize",
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "noteflow-worker", "version": "2"},
                },
            }
            response, response_headers = post_mcp_json(self.endpoint, initialize, headers=auth_headers)
            if response.get("error"):
                raise RuntimeError("MCP initialize error: " + json.dumps(response["error"])[:1000])
            result = response.get("result") or {}
            self.protocol_version = result.get("protocolVersion") or self.protocol_version
            self.session_id = response_headers.get("mcp-session-id") or "stateless"
            initialized_headers = {
                **auth_headers,
                "MCP-Protocol-Version": self.protocol_version,
                **({"Mcp-Session-Id": self.session_id} if self.session_id != "stateless" else {}),
            }
            post_mcp_json(
                self.endpoint,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=initialized_headers,
                allow_empty=True,
            )


class RouterVisionProvider:
    provider_name = "router"
    model = "multi-provider"

    def __init__(self, providers: list[VisionProvider]) -> None:
        self.providers = providers
        self._index = 0
        self._lock = Lock()
        self._failures = [0 for _ in providers]
        self._cooldown_until = [0.0 for _ in providers]

    def analyze(self, image_path: str, region: VisualRegion) -> VisionAnalysis:
        if not self.providers:
            return DisabledVisionProvider().analyze(image_path, region)
        with self._lock:
            start = self._index
            self._index = (self._index + 1) % len(self.providers)
            now = time.monotonic()
            available = [index for index, until in enumerate(self._cooldown_until) if until <= now]
            if not available:
                available = [min(range(len(self.providers)), key=self._cooldown_until.__getitem__)]
        errors: list[str] = []
        ordered = [index for index in ((start + offset) % len(self.providers) for offset in range(len(self.providers))) if index in available]
        for index in ordered:
            provider = self.providers[index]
            analysis = provider.analyze(image_path, region)
            if not analysis.error_message:
                with self._lock:
                    self._failures[index] = 0
                    self._cooldown_until[index] = 0.0
                return analysis
            with self._lock:
                self._failures[index] += 1
                error = analysis.error_message.lower()
                if any(marker in error for marker in ("429", "rate limit", "500", "502", "503", "504", "timeout")):
                    self._cooldown_until[index] = time.monotonic() + min(120.0, 2.0 ** self._failures[index])
                elif any(marker in error for marker in ("401", "403", "api key", "unauthorized")):
                    self._cooldown_until[index] = time.monotonic() + 300.0
            errors.append(f"{analysis.provider}/{analysis.model}: {analysis.error_message}")
        return VisionAnalysis(
            provider=self.provider_name,
            model=self.model,
            uncertainty="All configured vision providers failed.",
            error_message=" | ".join(errors)[:2000],
        )


def make_vision_provider() -> VisionProvider:
    provider = (settings.vision_provider or "disabled").lower().strip()
    candidates = build_provider_candidates(provider)
    if candidates:
        return candidates[0] if len(candidates) == 1 else RouterVisionProvider(candidates)
    return DisabledVisionProvider()


def build_provider_candidates(provider: str) -> list[VisionProvider]:
    requested = [item.strip() for item in provider.split(",") if item.strip()]
    if provider in {"auto", "router"}:
        requested = [item.strip() for item in settings.vision_provider_order.split(",") if item.strip()]
    candidates: list[VisionProvider] = []
    for name in requested:
        if name == "gemini":
            keys = parse_api_keys(settings.gemini_api_keys, settings.gemini_api_key)
            candidates.extend(GeminiVisionProvider(key) for key in keys)
        elif name == "openai":
            keys = parse_api_keys(settings.openai_api_keys, settings.openai_api_key)
            candidates.extend(OpenAIVisionProvider(key) for key in keys)
        elif name == "mcp" and settings.mcp_vision_endpoint:
            keys = parse_api_keys(settings.mcp_vision_api_keys, settings.mcp_vision_api_key, allow_empty=True)
            candidates.extend(McpVisionProvider(key) for key in keys)
    return candidates


def parse_api_keys(*values: str, allow_empty: bool = False) -> list[str]:
    keys: list[str] = []
    for value in values:
        for item in value.replace("\n", ",").split(","):
            key = item.strip()
            if key and key not in keys:
                keys.append(key)
    return keys or ([""] if allow_empty else [])


def vision_response_schema() -> dict:
    return {
        "type": "OBJECT",
        "properties": {key: {"type": "STRING"} for key in sorted(VISION_KEYS)},
        "required": sorted(VISION_KEYS),
    }


def openai_vision_response_schema() -> dict:
    return {
        "type": "object",
        "properties": {key: {"type": "string"} for key in sorted(VISION_KEYS)},
        "required": sorted(VISION_KEYS),
        "additionalProperties": False,
    }


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


def post_mcp_json(
    url: str,
    payload: dict,
    headers: dict | None = None,
    allow_empty: bool = False,
) -> tuple[dict, dict[str, str]]:
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **(headers or {}),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.vision_request_timeout_seconds) as response:
            body = response.read().decode("utf-8")
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            if not body.strip() and allow_empty:
                return {}, response_headers
            content_type = response_headers.get("content-type", "")
            if "text/event-stream" in content_type:
                messages = [
                    json.loads(line[5:].strip())
                    for line in body.splitlines()
                    if line.startswith("data:") and line[5:].strip()
                ]
                request_id = payload.get("id")
                matched = next((message for message in messages if message.get("id") == request_id), None)
                if matched is None:
                    raise RuntimeError("MCP SSE response did not contain the matching JSON-RPC id.")
                return matched, response_headers
            return json.loads(body), response_headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MCP HTTP {exc.code}: {body[:1000]}") from exc


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
    validate_vision_response(parsed)
    return VisionAnalysis(
        provider=provider,
        model=model,
        transcription=str(parsed.get("transcription", "")),
        description=str(parsed.get("description", "")),
        latex=str(parsed.get("latex", "")),
        code=str(parsed.get("code", "")),
        uncertainty=str(parsed.get("uncertainty", "")),
        search_text=str(parsed.get("search_text", "")),
        content_kind=str(parsed.get("content_kind", "unknown")),
        importance=str(parsed.get("importance", "medium")),
        reading_order=str(parsed.get("reading_order", "")),
        language=str(parsed.get("language", "unknown")),
        raw_response_json=json.dumps(redact_raw_response(raw_response), separators=(",", ":")),
    )


def validate_vision_response(parsed: dict) -> None:
    if not isinstance(parsed, dict) or set(parsed) != VISION_KEYS:
        raise ValueError("Vision response has invalid or missing fields.")
    if any(not isinstance(parsed[key], str) for key in VISION_KEYS):
        raise ValueError("Every vision response field must be a string.")
    if not any(parsed[key].strip() for key in ("transcription", "description", "latex", "code", "search_text")):
        raise ValueError("Vision response contains no usable extracted content.")
    if parsed["content_kind"] not in {
        "prose", "handwriting", "formula", "code", "table", "diagram", "chart",
        "screenshot", "decorative", "mixed", "unknown",
    }:
        raise ValueError("Vision response content_kind is invalid.")
    if parsed["importance"] not in {"high", "medium", "low"}:
        raise ValueError("Vision response importance is invalid.")


def redact_raw_response(response: dict) -> dict:
    return {
        "providerResponseKeys": sorted(response.keys()),
        "usageMetadata": response.get("usageMetadata") or response.get("usage"),
    }
