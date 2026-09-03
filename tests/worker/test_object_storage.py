import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from noteflow_worker.storage import ObjectStorage


PDF = b"%PDF-1.7\ncloud document"
PNG = b"\x89PNG\r\n\x1a\nartifact"
USER_ID = "00000000-0000-0000-0000-000000000021"
DOCUMENT_ID = "00000000-0000-0000-0000-000000000022"
SOURCE_PATH = f"users/{USER_ID}/documents/{DOCUMENT_ID}/source.pdf"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class ObjectStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.uploads: list[tuple[str, bytes | None, dict[str, str]]] = []
        self.urlopen_patch = patch("noteflow_worker.storage.urlopen", side_effect=self._urlopen)
        self.urlopen_patch.start()
        self.storage = ObjectStorage(
            supabase_url="https://project.supabase.co",
            secret_key="sb_secret_test",
            bucket="noteflow-private",
            timeout_seconds=2,
            max_attempts=1,
            max_download_bytes=1024,
        )

    def tearDown(self) -> None:
        self.urlopen_patch.stop()

    def test_materializes_private_document_in_ephemeral_directory(self) -> None:
        reference = f"supabase://noteflow-private/{SOURCE_PATH}"

        with self.storage.materialize_document(reference, DOCUMENT_ID, USER_ID) as local_path:
            materialized = Path(local_path)
            self.assertEqual(PDF, materialized.read_bytes())
            temporary_root = materialized.parents[1]
            self.assertTrue(temporary_root.exists())

        self.assertFalse(temporary_root.exists())

    def test_publishes_generated_png_with_deterministic_private_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            png = Path(temporary_directory) / "page-001.png"
            png.write_bytes(PNG)

            reference = self.storage.publish_png(png.as_posix(), USER_ID, DOCUMENT_ID, "rendered")

        self.assertEqual(
            f"supabase://noteflow-private/users/{USER_ID}/documents/{DOCUMENT_ID}/rendered/page-001.png",
            reference,
        )
        self.assertEqual(1, len(self.uploads))
        path, body, headers = self.uploads[0]
        self.assertEqual(
            f"https://project.supabase.co/storage/v1/object/noteflow-private/users/{USER_ID}/documents/{DOCUMENT_ID}/rendered/page-001.png",
            path,
        )
        self.assertEqual(PNG, body)
        self.assertEqual("true", headers["X-upsert"])

    def test_rejects_document_reference_for_another_user(self) -> None:
        reference = f"supabase://noteflow-private/{SOURCE_PATH}"

        with self.assertRaisesRegex(ValueError, "owner and id"):
            with self.storage.materialize_document(reference, DOCUMENT_ID, "another-user"):
                pass

    def test_rejects_generated_non_png(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            invalid = Path(temporary_directory) / "page.png"
            invalid.write_bytes(b"not png")

            with self.assertRaisesRegex(ValueError, "valid PNG"):
                self.storage.publish_png(invalid.as_posix(), USER_ID, DOCUMENT_ID, "rendered")

    def _urlopen(self, request, timeout):
        self.assertEqual(2, timeout)
        self.assertEqual("sb_secret_test", request.headers["Apikey"])
        self.assertNotIn("Authorization", request.headers)
        if request.get_method() == "GET":
            self.assertEqual(
                f"https://project.supabase.co/storage/v1/object/noteflow-private/{SOURCE_PATH}",
                request.full_url,
            )
            return _Response(PDF)
        self.uploads.append((request.full_url, request.data, request.headers))
        return _Response(b"{}")


if __name__ == "__main__":
    unittest.main()
