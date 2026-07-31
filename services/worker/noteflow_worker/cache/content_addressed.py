from __future__ import annotations

import hashlib
import json
from typing import Any

from noteflow_worker.db.repository import Repository


def content_hash(*parts: bytes | str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        value = part.encode("utf-8") if isinstance(part, str) else part
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


class ContentAddressedCache:
    """Small durable JSON CAS shared by all documents and worker processes."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def get(self, namespace: str, digest: str, producer_version: str) -> Any | None:
        with self.repository.connect() as conn:
            row = conn.execute(
                """
                UPDATE content_addressed_cache
                SET hit_count = hit_count + 1, last_accessed_at = NOW()
                WHERE namespace = %s AND content_hash = %s AND producer_version = %s
                RETURNING payload_json
                """,
                (namespace, digest, producer_version),
            ).fetchone()
        if not row:
            return None
        value = row["payload_json"]
        return json.loads(value) if isinstance(value, str) else value

    def put(self, namespace: str, digest: str, producer_version: str, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self.repository.connect() as conn:
            conn.execute(
                """
                INSERT INTO content_addressed_cache(namespace, content_hash, producer_version, payload_json)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT(namespace, content_hash, producer_version) DO UPDATE
                SET payload_json = EXCLUDED.payload_json, last_accessed_at = NOW()
                """,
                (namespace, digest, producer_version, encoded),
            )
