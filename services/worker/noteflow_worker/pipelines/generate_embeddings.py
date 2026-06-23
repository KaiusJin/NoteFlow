from __future__ import annotations

import hashlib

from noteflow_worker.config import settings
from noteflow_worker.db.repository import DocumentEmbedding, EmbeddingSource, Repository
from noteflow_worker.embeddings.providers import make_embedding_provider
from noteflow_worker.queue.redis_queue import TaskPayload


class GenerateEmbeddingsPipeline:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def run(self, payload: TaskPayload) -> None:
        try:
            self._repository.mark_task_processing(payload.task_id, "GENERATING_EMBEDDINGS", 5)
            self._repository.ensure_embedding_schema()
            document = self._repository.load_document(payload.document_id)
            sources = self._repository.load_embedding_sources(payload.document_id, include_pdf=True, include_ai_note=True)
            if not sources:
                raise RuntimeError("Cannot generate embeddings because no PDF chunks or AI note sections are available.")

            provider = make_embedding_provider()
            if provider.provider_name == "disabled":
                raise RuntimeError("Embedding provider is not configured. Set EMBEDDING_PROVIDER plus GEMINI_API_KEY.")

            source_hashes = {
                source_key(source): content_hash(source.embedding_text)
                for source in sources
            }
            existing_hashes = self._repository.existing_embedding_hashes(provider.provider_name, provider.model, sources)
            pending_sources = [
                source
                for source in sources
                if existing_hashes.get(source_key(source)) != source_hashes[source_key(source)]
            ]

            if not pending_sources:
                self._repository.mark_task_processing(payload.task_id, "GENERATING_EMBEDDINGS", 100)
                self._repository.mark_task_completed(payload.task_id)
                return

            embeddings_to_save: list[DocumentEmbedding] = []
            total = len(pending_sources)
            processed = 0
            for batch in batched(pending_sources, max(1, settings.embedding_batch_size)):
                results = provider.embed_texts([source.embedding_text for source in batch])
                if len(results) != len(batch):
                    raise RuntimeError("Embedding provider returned a different number of results than requested.")

                for source, result in zip(batch, results):
                    if result.error_message:
                        raise RuntimeError(
                            f"Embedding failed for {source.source_domain}/{source.source_object_type} "
                            f"{source.source_object_id}: {result.error_message}"
                        )
                    if not result.embedding:
                        raise RuntimeError(
                            f"Embedding provider returned an empty vector for {source.source_domain}/"
                            f"{source.source_object_type} {source.source_object_id}."
                        )
                    embeddings_to_save.append(
                        DocumentEmbedding(
                            document_id=document.id,
                            source_domain=source.source_domain,
                            source_object_type=source.source_object_type,
                            source_object_id=source.source_object_id,
                            embedding_provider=provider.provider_name,
                            embedding_model=provider.model,
                            embedding_dimension=len(result.embedding),
                            content_hash=source_hashes[source_key(source)],
                            embedding_text=source.embedding_text,
                            text_preview=source.text_preview,
                            embedding=result.embedding,
                            metadata_json=source.metadata_json,
                        )
                    )

                processed += len(batch)
                progress = 10 + int((processed / total) * 80)
                self._repository.mark_task_processing(payload.task_id, "GENERATING_EMBEDDINGS", progress)

            self._repository.upsert_embeddings(embeddings_to_save)
            self._repository.mark_task_completed(payload.task_id)
        except Exception as exc:
            self._repository.mark_task_failed(payload.task_id, str(exc))
            raise


def batched(items: list[EmbeddingSource], batch_size: int) -> list[list[EmbeddingSource]]:
    return [items[index:index + batch_size] for index in range(0, len(items), batch_size)]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_key(source: EmbeddingSource) -> tuple[str, str, str]:
    return (source.source_domain, source.source_object_type, source.source_object_id)
