"""Benchmark MuPDF page-render pool sizes on the deployment host."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

import fitz

from noteflow_worker.pdf.visual import analyze_pdf_visuals
from noteflow_worker.runtime.resource_pools import AcceleratorInfo, build_resource_pool_plan


class _NoOcr:
    name = "disabled"
    uses_gpu = False


def build_fixture(path: Path, page_count: int) -> None:
    document = fitz.open()
    for page_index in range(page_count):
        page = document.new_page(width=612, height=792)
        for row in range(18):
            page.insert_text(
                (36, 50 + row * 36),
                f"Page {page_index + 1} row {row}: benchmark text with formula x^2 + y^2 = z^2",
                fontsize=10,
            )
    document.save(path)
    document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=48)
    parser.add_argument("--workers", default="1,2,4,8")
    args = parser.parse_args()
    worker_counts = [int(value) for value in args.workers.split(",")]

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        upload_dir = root / "storage" / "uploads"
        upload_dir.mkdir(parents=True)
        pdf_path = upload_dir / "pool-benchmark.pdf"
        build_fixture(pdf_path, args.pages)

        import noteflow_worker.pdf.visual as visual_module

        original_factory = visual_module.make_ocr_backend
        visual_module.make_ocr_backend = lambda accelerator=None: _NoOcr()
        try:
            for workers in worker_counts:
                plan = build_resource_pool_plan(
                    configured_cpu_workers=workers,
                    accelerator=AcceleratorInfo("cpu", False),
                )
                started = time.perf_counter()
                pages = analyze_pdf_visuals(str(pdf_path), f"benchmark-{workers}", plan)
                elapsed = time.perf_counter() - started
                print(
                    f"workers={workers} pages={len(pages)} elapsed={elapsed:.4f}s "
                    f"pages_per_second={len(pages) / elapsed:.2f}"
                )
        finally:
            visual_module.make_ocr_backend = original_factory


if __name__ == "__main__":
    main()
