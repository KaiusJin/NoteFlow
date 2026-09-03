from __future__ import annotations

import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from noteflow_worker.config import settings

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ObjectStorage:
    def __init__(
        self,
        *,
        supabase_url: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        timeout_seconds: float | None = None,
        max_attempts: int | None = None,
        max_download_bytes: int | None = None,
        max_png_bytes: int | None = None,
    ) -> None:
        self._supabase_url = (supabase_url if supabase_url is not None else settings.supabase_url).rstrip("/")
        configured_key = settings.supabase_secret_key or settings.supabase_service_role_key
        self._secret_key = secret_key if secret_key is not None else configured_key
        self._bucket = bucket if bucket is not None else settings.supabase_storage_bucket
        self._timeout = max(1.0, timeout_seconds or settings.storage_http_timeout_seconds)
        self._max_attempts = max(1, max_attempts or settings.storage_request_max_attempts)
        self._max_download_bytes = max(1, max_download_bytes or settings.storage_max_download_bytes)
        self._max_png_bytes = max(1, max_png_bytes or settings.storage_max_png_bytes)

    @staticmethod
    def is_remote(storage_path: str) -> bool:
        return urlparse(storage_path).scheme == "supabase"

    @contextmanager
    def materialize_document(
        self,
        storage_path: str,
        document_id: str,
        user_id: str,
    ) -> Iterator[str]:
        if not self.is_remote(storage_path):
            yield storage_path
            return

        reference_bucket, object_path = self._parse_reference(storage_path)
        expected_path = f"users/{user_id}/documents/{document_id}/source.pdf"
        if object_path != expected_path:
            raise ValueError("Document storage object does not match its database owner and id")

        with tempfile.TemporaryDirectory(prefix=f"noteflow-{document_id}-") as temporary_directory:
            upload_dir = Path(temporary_directory) / "uploads"
            upload_dir.mkdir(parents=True)
            local_path = upload_dir / f"{document_id}.pdf"
            self._download(reference_bucket, object_path, local_path)
            yield str(local_path)

    def publish_png(
        self,
        local_path: str,
        user_id: str,
        document_id: str,
        category: str,
    ) -> str:
        if category not in {"rendered", "regions"}:
            raise ValueError("Unsupported storage artifact category")
        path = Path(local_path)
        if path.suffix.lower() != ".png" or not path.is_file():
            raise ValueError("Only generated PNG files can be published")
        with path.open("rb") as source:
            if source.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
                raise ValueError("Generated artifact is not a valid PNG")
        if path.stat().st_size > self._max_png_bytes:
            raise ValueError("Generated PNG exceeds the configured object limit")
        object_path = f"users/{user_id}/documents/{document_id}/{category}/{path.name}"
        self._upload(self._bucket, object_path, path.read_bytes(), "image/png", upsert=True)
        return self._object_reference(self._bucket, object_path)

    def _download(self, bucket: str, object_path: str, destination: Path) -> None:
        self._require_remote_configuration()
        url = self._object_url(bucket, object_path)
        for attempt in range(1, self._max_attempts + 1):
            try:
                request = Request(url, headers=self._headers(), method="GET")
                with urlopen(request, timeout=self._timeout) as response, destination.open("wb") as output:
                    copied = 0
                    while chunk := response.read(64 * 1024):
                        copied += len(chunk)
                        if copied > self._max_download_bytes:
                            raise ValueError("Stored PDF exceeds the configured download limit")
                        output.write(chunk)
                return
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                destination.unlink(missing_ok=True)
                if not self._should_retry(error, attempt):
                    raise RuntimeError(f"Could not download document from object storage: {type(error).__name__}") from error
                self._backoff(attempt)

    def _upload(self, bucket: str, object_path: str, content: bytes, content_type: str, *, upsert: bool) -> None:
        self._require_remote_configuration()
        headers = {
            **self._headers(),
            "Content-Type": content_type,
            "cache-control": "max-age=3600",
            "x-upsert": str(upsert).lower(),
        }
        for attempt in range(1, self._max_attempts + 1):
            try:
                request = Request(
                    self._object_url(bucket, object_path),
                    data=content,
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=self._timeout):
                    return
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                if not self._should_retry(error, attempt):
                    raise RuntimeError(f"Could not upload generated artifact: {type(error).__name__}") from error
                self._backoff(attempt)

    def _headers(self) -> dict[str, str]:
        headers = {"apikey": self._secret_key}
        if self._secret_key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {self._secret_key}"
        return headers

    def _parse_reference(self, storage_path: str) -> tuple[str, str]:
        reference = urlparse(storage_path)
        object_path = reference.path.lstrip("/")
        if (
            reference.scheme != "supabase"
            or reference.netloc != self._bucket
            or not object_path
            or ".." in object_path.split("/")
            or "" in object_path.split("/")
        ):
            raise ValueError("Storage object is outside the configured private bucket")
        return reference.netloc, object_path

    def _object_url(self, bucket: str, object_path: str) -> str:
        encoded_path = "/".join(quote(segment, safe="") for segment in object_path.split("/"))
        return f"{self._supabase_url}/storage/v1/object/{quote(bucket, safe='')}/{encoded_path}"

    @staticmethod
    def _object_reference(bucket: str, object_path: str) -> str:
        return f"supabase://{bucket}/{object_path}"

    def _require_remote_configuration(self) -> None:
        if not self._supabase_url or not self._secret_key or not self._bucket:
            raise RuntimeError("Supabase object storage is not configured for this worker")

    def _should_retry(self, error: Exception, attempt: int) -> bool:
        if attempt >= self._max_attempts:
            return False
        if isinstance(error, HTTPError) and error.code < 500 and error.code != 429:
            return False
        return True

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))
