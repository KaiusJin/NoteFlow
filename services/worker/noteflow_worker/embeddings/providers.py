from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Protocol

from noteflow_worker.config import settings


@dataclass(frozen=True)
class EmbeddingResult:
    embedding: list[float]
    error_message: str = ""


class EmbeddingProvider(Protocol):
    provider_name: str
    model: str
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        ...


class DisabledEmbeddingProvider:
    provider_name = "disabled"
    model = "none"
    dimension = 0

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        return [EmbeddingResult([], "Embedding provider is not configured.") for _ in texts]


class GeminiEmbeddingProvider:
    provider_name = "gemini"
    dimension = 768

    def __init__(self) -> None:
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_embedding_model

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        if not self.api_key:
            return [EmbeddingResult([], "Gemini API key is not configured.") for _ in texts]
        max_workers = max(1, min(settings.embedding_max_concurrent_requests, len(texts)))
        results: list[EmbeddingResult | None] = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {
                executor.submit(embedding_with_retries, lambda text=text: self.embed_one(text)): index
                for index, text in enumerate(texts)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    results[index] = EmbeddingResult([], str(exc)[:2000])
        return [result or EmbeddingResult([], "Embedding request did not return a result.") for result in results]

    def embed_one(self, text: str) -> EmbeddingResult:
        model_name = self.model if self.model.startswith("models/") else f"models/{self.model}"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:embedContent?key={self.api_key}"
        payload = {
            "model": model_name,
            "content": {"parts": [{"text": text}]},
        }
        response = post_json(url, payload)
        values = response.get("embedding", {}).get("values", [])
        if not isinstance(values, list) or not values:
            raise RuntimeError("Gemini embedding response did not contain embedding.values.")
        return EmbeddingResult([float(value) for value in values])


class OpenAIEmbeddingProvider:
    provider_name = "openai"
    dimension = 1536

    def __init__(self) -> None:
        self.api_key = settings.openai_api_key
        self.model = settings.openai_embedding_model

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        return [EmbeddingResult([], "OpenAI embedding provider is reserved but not implemented yet.") for _ in texts]


class LocalEmbeddingProvider:
    provider_name = "local"
    dimension = 0

    def __init__(self) -> None:
        self.model = settings.local_embedding_model

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        return [EmbeddingResult([], "Local embedding provider is reserved but not implemented yet.") for _ in texts]


def make_embedding_provider() -> EmbeddingProvider:
    provider = (settings.embedding_provider or "").strip().lower()
    if provider == "gemini":
        return GeminiEmbeddingProvider()
    if provider == "openai":
        return OpenAIEmbeddingProvider()
    if provider == "local":
        return LocalEmbeddingProvider()
    return DisabledEmbeddingProvider()


def embedding_with_retries(request_fn) -> EmbeddingResult:
    last_error = ""
    for attempt in range(1, max(1, settings.notes_request_max_attempts) + 1):
        try:
            return request_fn()
        except Exception as exc:
            last_error = str(exc)[:2000]
            if attempt >= settings.notes_request_max_attempts or not is_retryable_error(last_error):
                break
            time.sleep(settings.notes_retry_backoff_seconds * attempt)
    return EmbeddingResult([], last_error or "Embedding request failed.")


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.notes_request_timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def is_retryable_error(message: str) -> bool:
    lowered = message.lower()
    retryable_terms = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "remote end closed",
        "http 408",
        "http 409",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
    )
    return any(term in lowered for term in retryable_terms)
