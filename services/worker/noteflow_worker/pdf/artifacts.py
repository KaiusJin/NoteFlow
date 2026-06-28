from __future__ import annotations

from pathlib import Path

from noteflow_worker.db.repository import VisualRegion
from noteflow_worker.pdf.visual import VisualPage


def cleanup_orphaned_pdf_artifacts(
    pdf_path: str,
    document_id: str,
    visual_pages: list[VisualPage],
    visual_regions: list[VisualRegion],
) -> list[str]:
    """Delete only generated files not referenced by the completed parse."""
    pdf = Path(pdf_path)
    storage_root = pdf.parent.parent
    managed_dirs = [storage_root / "rendered" / document_id, storage_root / "regions" / document_id]
    keep = {
        Path(page.image_path).resolve() for page in visual_pages
    } | {
        Path(region.asset_path).resolve() for region in visual_regions
    }
    removed: list[str] = []
    for directory in managed_dirs:
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.resolve() in keep:
                continue
            path.unlink(missing_ok=True)
            removed.append(str(path))
        for child in sorted(directory.rglob("*"), reverse=True):
            if child.is_dir() and not any(child.iterdir()):
                child.rmdir()
    return removed
