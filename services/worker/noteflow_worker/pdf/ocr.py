from __future__ import annotations

import shutil
from threading import Lock
from typing import Protocol

from PIL import Image

from noteflow_worker.config import settings
from noteflow_worker.runtime.resource_pools import AcceleratorInfo


class OcrBackend(Protocol):
    name: str
    uses_gpu: bool

    def recognize(self, image_path: str) -> str:
        ...


class DisabledOcrBackend:
    name = "disabled"
    uses_gpu = False

    def recognize(self, image_path: str) -> str:
        return ""


class TesseractOcrBackend:
    name = "tesseract"
    uses_gpu = False

    def recognize(self, image_path: str) -> str:
        import pytesseract

        return pytesseract.image_to_string(Image.open(image_path))


class PaddleOcrBackend:
    name = "paddleocr"

    def __init__(self, use_gpu: bool) -> None:
        from paddleocr import PaddleOCR  # type: ignore

        self.uses_gpu = use_gpu
        self._lock = Lock()
        language = settings.pdf_ocr_languages.split(",")[0].strip() or "en"
        try:
            self._engine = PaddleOCR(use_angle_cls=True, lang=language, use_gpu=use_gpu, show_log=False)
        except TypeError:
            # PaddleOCR 3.x moved device selection to a string parameter.
            self._engine = PaddleOCR(use_doc_orientation_classify=True, lang=language, device="gpu" if use_gpu else "cpu")

    def recognize(self, image_path: str) -> str:
        # A single model instance is not guaranteed to be thread-safe. Multiple
        # GPU workers are realized as separate pipeline instances at deployment;
        # this local instance serializes access to its model weights.
        with self._lock:
            result = self._engine.ocr(image_path, cls=True)
        lines: list[str] = []
        for page in result or []:
            for item in page or []:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    value = item[1]
                    if isinstance(value, (list, tuple)) and value:
                        lines.append(str(value[0]))
        return "\n".join(lines)


class EasyOcrBackend:
    name = "easyocr"
    uses_gpu = True

    def __init__(self) -> None:
        import easyocr  # type: ignore

        languages = [value.strip() for value in settings.pdf_ocr_languages.split(",") if value.strip()] or ["en"]
        self._reader = easyocr.Reader(languages, gpu=True)
        self._lock = Lock()

    def recognize(self, image_path: str) -> str:
        with self._lock:
            lines = self._reader.readtext(image_path, detail=0, paragraph=False)
        return "\n".join(str(line) for line in lines)


def make_ocr_backend(accelerator: AcceleratorInfo | None = None) -> OcrBackend:
    requested = settings.pdf_ocr_backend.lower().strip()
    device = accelerator or AcceleratorInfo(kind="cpu", available=False)
    wants_easyocr = requested == "easyocr" or (requested == "auto" and device.kind == "mps" and device.available)
    if wants_easyocr:
        try:
            return EasyOcrBackend()
        except (ImportError, RuntimeError, OSError):
            if requested == "easyocr":
                raise
    wants_paddle = requested == "paddleocr" or (requested == "auto" and device.available)
    if wants_paddle:
        try:
            return PaddleOcrBackend(use_gpu=bool(device.available and settings.pdf_enable_gpu_ocr))
        except (ImportError, RuntimeError, OSError):
            if requested == "paddleocr":
                raise
    if requested in {"auto", "tesseract"} and shutil.which("tesseract"):
        return TesseractOcrBackend()
    return DisabledOcrBackend()


def clean_ocr_text(text: str, minimum_chars: int = 20, maximum_chars: int = 12000) -> str | None:
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(cleaned) < minimum_chars:
        return None
    return cleaned[:maximum_chars]
