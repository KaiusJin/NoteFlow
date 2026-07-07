from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Callable

from noteflow_worker.config import settings
from noteflow_worker.notes.providers import (
    convert_gemini_schema_to_json_schema,
    is_retryable_error,
    parse_json_object,
)


class MemoryLlmError(RuntimeError):
    pass


class StructuredMemoryLlm:
    """Provider-neutral JSON-mode client for summarization and extraction.

    Both operations share one retry policy: transient HTTP failures and
    stochastic structured-output validation failures are retried with
    exponential backoff plus jitter; deterministic failures are not.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        request_fn: Callable | None = None,
        *,
        timeout_seconds: int | None = None,
        max_attempts: int | None = None,
        backoff_seconds: float | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._request_fn = request_fn
        self.timeout_seconds = timeout_seconds or settings.memory_request_timeout_seconds
        self.max_attempts = max(1, max_attempts or settings.memory_request_max_attempts)
        self.backoff_seconds = backoff_seconds or settings.memory_retry_backoff_seconds

    def generate(self, prompt: str, response_schema: dict, schema_name: str, validate: Callable[[dict], None]) -> dict:
        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._request(prompt, response_schema, schema_name)
                parsed = parse_json_object(extract_response_text(response))
                validate(parsed)
                return parsed
            except Exception as exc:
                last_error = str(exc)[:2000]
                retryable = isinstance(exc, (ValueError, json.JSONDecodeError)) or is_retryable_error(last_error)
                if attempt >= self.max_attempts or not retryable:
                    break
                base = self.backoff_seconds * (2 ** (attempt - 1))
                jitter = random.uniform(0.0, min(1.0, self.backoff_seconds * 0.25))
                time.sleep(min(30.0, base + jitter))
        raise MemoryLlmError(last_error or "Memory LLM call failed.")

    def _request(self, prompt: str, response_schema: dict, schema_name: str) -> dict:
        if self._request_fn is not None:
            return self._request_fn(prompt, response_schema, schema_name)
        if self.provider == "gemini":
            return self._request_gemini(prompt, response_schema)
        if self.provider == "openai":
            return self._request_openai(prompt, response_schema, schema_name)
        raise MemoryLlmError(f"Unsupported memory LLM provider: {self.provider}")

    def _request_gemini(self, prompt: str, response_schema: dict) -> dict:
        if not settings.gemini_api_key:
            raise MemoryLlmError("Gemini API key is not configured.")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + self.model
            + ":generateContent?key="
            + settings.gemini_api_key
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "response_mime_type": "application/json",
                "response_schema": response_schema,
            },
        }
        return post_json(url, payload, timeout_seconds=self.timeout_seconds)

    def _request_openai(self, prompt: str, response_schema: dict, schema_name: str) -> dict:
        if not settings.openai_api_key:
            raise MemoryLlmError("OpenAI API key is not configured.")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": convert_gemini_schema_to_json_schema(response_schema),
                },
            },
        }
        headers = {"Authorization": "Bearer " + settings.openai_api_key}
        return post_json("https://api.openai.com/v1/chat/completions", payload, headers=headers, timeout_seconds=self.timeout_seconds)


def extract_response_text(response: dict) -> str:
    if "candidates" in response:
        return response["candidates"][0]["content"]["parts"][0].get("text", "")
    if "choices" in response:
        return response["choices"][0]["message"].get("content", "")
    return json.dumps(response)


def post_json(url: str, payload: dict, headers: dict | None = None, timeout_seconds: int | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds or settings.memory_request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Memory LLM HTTP {exc.code}: {body[:1000]}") from exc


def make_memory_llm() -> StructuredMemoryLlm:
    provider = (settings.memory_llm_provider or settings.notes_provider or "").lower().strip()
    if not provider:
        if settings.gemini_api_key:
            provider = "gemini"
        elif settings.openai_api_key:
            provider = "openai"
    if provider == "gemini":
        return StructuredMemoryLlm("gemini", settings.memory_gemini_model or settings.gemini_notes_model)
    if provider == "openai":
        return StructuredMemoryLlm("openai", settings.memory_openai_model or settings.openai_notes_model)
    raise MemoryLlmError(
        "Memory LLM is not configured. Set MEMORY_LLM_PROVIDER or NOTES_PROVIDER plus GEMINI_API_KEY or OPENAI_API_KEY."
    )
