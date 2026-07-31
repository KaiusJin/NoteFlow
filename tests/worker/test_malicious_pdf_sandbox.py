import os
import tempfile
import unittest
from pathlib import Path

from noteflow_worker.pdf.parser import parse_pdf


class MaliciousPdfSandboxTest(unittest.TestCase):
    def test_malformed_or_active_content_pdf_fails_closed(self):
        payloads = [
        b"%PDF-1.7\n" + b"\x00" * 4096,
        b"%PDF-1.7\n1 0 obj\n<</Length 999999999>>\nstream\n",
        b"%PDF-1.7\n/JavaScript (app.launchURL('file:///tmp/owned'))\n%%EOF",
        os.urandom(2048),
        ]
        for payload in payloads:
            with self.subTest(size=len(payload)), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "hostile.pdf"
                target.write_bytes(payload)
                with self.assertRaises(Exception):
                    parse_pdf(str(target), "OTHER")
                self.assertEqual([target], list(Path(directory).iterdir()))
