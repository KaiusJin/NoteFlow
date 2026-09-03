from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from noteflow_worker.config import ai_setting, settings
from noteflow_worker.runtime.limits import process_resource_slot


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
        self.api_key = ai_setting("gemini_api_key")
        self.model = ai_setting("gemini_embedding_model")

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        if not self.api_key:
            return [EmbeddingResult([], "Gemini API key is not configured.") for _ in texts]
        # batchEmbedContents accepts up to 100 requests per call, so a large
        # document costs a handful of HTTP round trips instead of one per chunk.
        chunks = [texts[i:i + GEMINI_EMBED_BATCH_LIMIT] for i in range(0, len(texts), GEMINI_EMBED_BATCH_LIMIT)]
        max_workers = max(1, min(settings.embedding_max_concurrent_requests, len(chunks)))
        results: list[EmbeddingResult | None] = [None] * len(texts)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_range = {
                executor.submit(embedding_with_retries, lambda chunk=chunk, start=i: self.embed_batch(chunk, start)): (
                    i,
                    i + len(chunk),
                )
                for i, chunk in enumerate(chunks)
            }
            for future, (start, end) in future_to_range.items():
                try:
                    batch_results = future.result()
                except Exception as exc:
                    batch_results = [EmbeddingResult([], str(exc)[:2000])] * (end - start)
                results[start:end] = batch_results
        return [result or EmbeddingResult([], "Embedding request did not return a result.") for result in results]

    def embed_batch(self, texts: list[str], start_index: int) -> list[EmbeddingResult]:
        model_name = self.model if self.model.startswith("models/") else f"models/{self.model}"
        url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:batchEmbedContents"
        payload = {
            "requests": [
                {"model": model_name, "content": {"parts": [{"text": text}]}}
                for text in texts
            ],
        }
        response = post_json(url, payload, headers={"x-goog-api-key": self.api_key})
        embeddings = response.get("embeddings", [])
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError("Gemini batch embedding response count mismatch.")
        results: list[EmbeddingResult] = []
        for index, entry in enumerate(embeddings):
            values = entry.get("values", [])
            if not isinstance(values, list) or not values:
                results.append(EmbeddingResult([], f"Gemini embedding response item {start_index + index} had no values."))
                continue
            results.append(EmbeddingResult([float(value) for value in values]))
        return results


class OpenAIEmbeddingProvider:
    provider_name = "openai"

    def __init__(self) -> None:
        self.api_key = ai_setting("openai_api_key")
        self.model = ai_setting("openai_embedding_model")
        self.dimension = resolve_openai_dimensions(self.model)

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        if not self.api_key:
            return [EmbeddingResult([], "OpenAI API key is not configured.") for _ in texts]
        payload = {"model": self.model, "input": texts}
        response = post_json(
            "https://api.openai.com/v1/embeddings",
            payload,
            headers={"Authorization": "Bearer " + self.api_key},
        )
        data = response.get("data", [])
        if not isinstance(data, list) or len(data) != len(texts):
            return [EmbeddingResult([], "OpenAI embedding response count mismatch.") for _ in texts]
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        return [
            EmbeddingResult([float(value) for value in item.get("embedding", [])])
            if item.get("embedding")
            else EmbeddingResult([], "OpenAI embedding response contained an empty vector.")
            for item in ordered
        ]


class LocalEmbeddingProvider:
    provider_name = "local"
    dimension = 0

    def __init__(self) -> None:
        self.model = settings.local_embedding_model

    def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        return [EmbeddingResult([], "Local embedding provider is reserved but not implemented yet.") for _ in texts]


def make_embedding_provider() -> EmbeddingProvider:
    provider = (ai_setting("embedding_provider") or "").strip().lower()
    if provider == "gemini":
        return GeminiEmbeddingProvider()
    if provider == "openai":
        return OpenAIEmbeddingProvider()
    if provider == "local":
        return LocalEmbeddingProvider()
    return DisabledEmbeddingProvider()


GEMINI_EMBED_BATCH_LIMIT = 100


def resolve_openai_dimensions(model: str) -> int:
    # The pgvector column dimension is fixed, so the configured model must map
    # to the declared dimension. text-embedding-3-large emits 3072 dims;
    # every other OpenAI embedding model emits 1536.
    override = settings.openai_embedding_dimensions
    if override > 0:
        return override
    if "3-large" in model:
        return 3072
    return 1536


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


def post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with process_resource_slot("embedding_provider", settings.embedding_max_concurrent_requests):
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
