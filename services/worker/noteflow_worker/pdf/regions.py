import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import fitz
from PIL import Image

from noteflow_worker.config import settings
from noteflow_worker.db.repository import VisualRegion, VlmResult
from noteflow_worker.pdf.visual import VisualPage
from noteflow_worker.vision.providers import VisionAnalysis, make_vision_provider

MIN_REGION_AREA_RATIO = 0.03
FULL_PAGE_FALLBACK_MIN_IMAGE_COVERAGE = 0.12
LOW_NATIVE_TEXT_LENGTH = 160


def compute_image_hash(image: Image.Image) -> str:
    """Computes a simple Average Hash (aHash) for PIL image perceptual comparison."""
    try:
        img = image.convert('L').resize((8, 8), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / 64
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        return f"{int(bits, 2):016x}"
    except Exception:
        return ""


def build_visual_regions(
    pdf_path: str,
    document_id: str,
    document_type: str,
    visual_pages: list[VisualPage],
    page_asset_ids: dict[int, str],
) -> list[VisualRegion]:
    visual_by_page = {page.page_number: page for page in visual_pages}
    output_dir = Path(pdf_path).parent.parent / "regions" / document_id
    output_dir.mkdir(parents=True, exist_ok=True)

    all_raw_regions: list[VisualRegion] = []
    with fitz.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            visual_page = visual_by_page.get(page_number)
            if not visual_page or not visual_page.has_visual_content:
                continue
            page_regions = crop_page_regions(
                page=page,
                visual_page=visual_page,
                document_id=document_id,
                document_type=document_type,
                output_dir=output_dir,
                page_asset_id=page_asset_ids.get(page_number),
            )
            all_raw_regions.extend(page_regions)
            
    # Calculate hash frequencies across the document
    hashes = []
    for r in all_raw_regions:
        try:
            meta = json.loads(r.metadata_json or "{}")
            image_hash = meta.get("imageHash", "")
            if image_hash:
                hashes.append(image_hash)
        except Exception:
            pass
            
    hash_counts = Counter(hashes)
    total_pages = len(visual_pages)
    # Define repetition threshold (appearing in more than 15% of pages, min 3 times)
    repeat_threshold = max(3, math.ceil(total_pages * 0.15))
    
    # Filter regions: drop repetitive hashes (background decoration, logo, etc.)
    filtered_regions: list[VisualRegion] = []
    regions_by_page: dict[int, list[VisualRegion]] = {}
    
    for r in all_raw_regions:
        try:
            meta = json.loads(r.metadata_json or "{}")
            image_hash = meta.get("imageHash", "")
            if image_hash and hash_counts[image_hash] > repeat_threshold and can_drop_repeated_region(r):
                # Discard repetitive/decorative image
                try:
                    Path(r.asset_path).unlink(missing_ok=True)
                except Exception:
                    pass
                continue
        except Exception:
            pass
        regions_by_page.setdefault(r.page_number, []).append(r)
        
    add_missing_page_fallback_regions(
        regions_by_page=regions_by_page,
        visual_pages=visual_pages,
        document_id=document_id,
        document_type=document_type,
        output_dir=output_dir,
        page_asset_ids=page_asset_ids,
    )

    # Re-index remaining regions per page
    for page_num, p_regions in sorted(regions_by_page.items()):
        for index, r in enumerate(p_regions):
            updated_region = VisualRegion(
                document_id=r.document_id,
                page_number=r.page_number,
                region_index=index,
                region_type=r.region_type,
                asset_path=r.asset_path,
                bbox_json=r.bbox_json,
                page_asset_id=r.page_asset_id,
                width=r.width,
                height=r.height,
                confidence=r.confidence,
                metadata_json=r.metadata_json,
            )
            filtered_regions.append(updated_region)
            if len(filtered_regions) >= settings.vision_max_regions_per_document:
                return filtered_regions[: settings.vision_max_regions_per_document]
                
    return filtered_regions


def can_drop_repeated_region(region: VisualRegion) -> bool:
    if region.region_type in {"CODE_IMAGE", "HANDWRITTEN", "FULL_PAGE_VISUAL"}:
        return False
    try:
        meta = json.loads(region.metadata_json or "{}")
        image_coverage = float(meta.get("imageCoverage") or 0.0)
    except Exception:
        image_coverage = 0.0
    return image_coverage < FULL_PAGE_FALLBACK_MIN_IMAGE_COVERAGE


def add_missing_page_fallback_regions(
    regions_by_page: dict[int, list[VisualRegion]],
    visual_pages: list[VisualPage],
    document_id: str,
    document_type: str,
    output_dir: Path,
    page_asset_ids: dict[int, str],
) -> None:
    for visual_page in visual_pages:
        if regions_by_page.get(visual_page.page_number):
            continue
        if not should_add_full_page_fallback(visual_page):
            continue
        regions_by_page[visual_page.page_number] = [
            create_full_page_region(
                visual_page=visual_page,
                document_id=document_id,
                document_type=document_type,
                output_dir=output_dir,
                page_asset_id=page_asset_ids.get(visual_page.page_number),
                source="missing_region_full_page_fallback",
            )
        ]


def should_add_full_page_fallback(visual_page: VisualPage) -> bool:
    if not visual_page.has_visual_content:
        return False
    if visual_page.image_coverage >= FULL_PAGE_FALLBACK_MIN_IMAGE_COVERAGE:
        return True
    return visual_page.image_count > 0 and visual_page.text_length <= LOW_NATIVE_TEXT_LENGTH


def create_full_page_region(
    visual_page: VisualPage,
    document_id: str,
    document_type: str,
    output_dir: Path,
    page_asset_id: Optional[str],
    source: str,
) -> VisualRegion:
    page_image = Image.open(visual_page.image_path)
    page_width, page_height = page_image.size
    region_path = output_dir / f"page-{visual_page.page_number:03d}-region-full.png"
    page_image.save(region_path)
    image_hash = compute_image_hash(page_image)
    return VisualRegion(
        document_id=document_id,
        page_number=visual_page.page_number,
        region_index=0,
        region_type=classify_region_type(visual_page, document_type, 1.0),
        asset_path=str(region_path),
        bbox_json=None,
        page_asset_id=page_asset_id,
        width=page_width,
        height=page_height,
        confidence=0.66,
        metadata_json=json.dumps(
            {
                "source": source,
                "pageImagePath": visual_page.image_path,
                "imageCoverage": visual_page.image_coverage,
                "imageHash": image_hash,
            },
            separators=(",", ":"),
        ),
    )


def crop_page_regions(
    page,
    visual_page: VisualPage,
    document_id: str,
    document_type: str,
    output_dir: Path,
    page_asset_id: Optional[str],
) -> list[VisualRegion]:
    page_image = Image.open(visual_page.image_path)
    page_width, page_height = page_image.size
    scale_x = page_width / max(float(page.rect.width), 1.0)
    scale_y = page_height / max(float(page.rect.height), 1.0)
    candidates = image_block_bboxes(page)
    regions: list[VisualRegion] = []

    for index, bbox in enumerate(candidates):
        if bbox_area_ratio(bbox, page.rect) < MIN_REGION_AREA_RATIO:
            continue
        crop_box = bbox_to_pixels(bbox, scale_x, scale_y, page_width, page_height, padding=16)
        
        # Crop region
        crop = page_image.crop(crop_box)
        width, height = crop.size
        
        # Geometric filter
        if width < 40 or height < 40:
            continue
        aspect_ratio = width / max(1.0, height)
        if aspect_ratio > 12.0 or aspect_ratio < 1.0 / 12.0:
            continue
            
        region_path = output_dir / f"page-{visual_page.page_number:03d}-region-{len(regions):02d}.png"
        crop.save(region_path)
        
        # Compute pHash
        image_hash = compute_image_hash(crop)
        
        regions.append(
            VisualRegion(
                document_id=document_id,
                page_number=visual_page.page_number,
                region_index=len(regions),
                region_type=classify_region_type(visual_page, document_type, bbox_area_ratio(bbox, page.rect)),
                asset_path=str(region_path),
                bbox_json=json.dumps([round(float(value), 2) for value in bbox], separators=(",", ":")),
                page_asset_id=page_asset_id,
                width=width,
                height=height,
                confidence=0.78,
                metadata_json=json.dumps(
                    {
                        "source": "embedded_image_block",
                        "pageImagePath": visual_page.image_path,
                        "imageCoverage": visual_page.image_coverage,
                        "imageHash": image_hash,
                    },
                    separators=(",", ":"),
                ),
            )
        )

    if not regions:
        regions.append(
            create_full_page_region(
                visual_page=visual_page,
                document_id=document_id,
                document_type=document_type,
                output_dir=output_dir,
                page_asset_id=page_asset_id,
                source="full_page_visual_fallback",
            )
        )
    return regions


def image_block_bboxes(page) -> list[tuple[float, float, float, float]]:
    bboxes: list[tuple[float, float, float, float]] = []
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = block.get("bbox")
        if bbox and len(bbox) == 4:
            bboxes.append(tuple(float(value) for value in bbox))
    return bboxes


def bbox_area_ratio(bbox: tuple[float, float, float, float], page_rect) -> float:
    x0, y0, x1, y1 = bbox
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    page_area = max(float(page_rect.width * page_rect.height), 1.0)
    return min(area / page_area, 1.0)


def bbox_to_pixels(
    bbox: tuple[float, float, float, float],
    scale_x: float,
    scale_y: float,
    page_width: int,
    page_height: int,
    padding: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return (
        max(0, int(x0 * scale_x) - padding),
        max(0, int(y0 * scale_y) - padding),
        min(page_width, int(x1 * scale_x) + padding),
        min(page_height, int(y1 * scale_y) + padding),
    )


def classify_region_type(visual_page: VisualPage, document_type: str, area_ratio: float) -> str:
    if document_type == "HANDWRITTEN_NOTES":
        return "HANDWRITTEN"
    if visual_page.text_length < 120 and visual_page.image_count > 0:
        return "CODE_IMAGE"
    if area_ratio > 0.65:
        return "FULL_PAGE_VISUAL"
    if visual_page.drawing_count >= 8:
        return "DIAGRAM"
    return "IMAGE"


def analyze_regions_with_vlm(regions: list[VisualRegion], fail_on_error: bool = False) -> list[VlmResult]:
    provider = make_vision_provider()
    results: list[VlmResult] = []
    for region in regions:
        analysis = analyze_region_with_retries(provider, region)
        results.append(to_vlm_result(region, analysis))
    if fail_on_error:
        failures = [result for result in results if result.error_message]
        if failures:
            details = "; ".join(
                f"page {result.page_number} region {result.region_index}: {result.error_message}"
                for result in failures[:5]
            )
            raise RuntimeError(f"Required VLM analysis failed after retries: {details}")
    return results


def analyze_region_with_retries(provider, region: VisualRegion) -> VisionAnalysis:
    max_attempts = max(1, settings.vision_request_max_attempts)
    last_analysis: VisionAnalysis | None = None
    for attempt in range(1, max_attempts + 1):
        analysis = provider.analyze(region.asset_path, region)
        if not analysis.error_message:
            return analysis
        last_analysis = analysis
        if attempt >= max_attempts or not is_retryable_vision_error(analysis.error_message):
            return analysis
        time.sleep(settings.vision_retry_backoff_seconds * attempt)
    return last_analysis or provider.analyze(region.asset_path, region)


def is_retryable_vision_error(error_message: str) -> bool:
    lowered = error_message.lower()
    retryable_markers = (
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
    return any(marker in lowered for marker in retryable_markers)


def to_vlm_result(region: VisualRegion, analysis: VisionAnalysis) -> VlmResult:
    return VlmResult(
        document_id=region.document_id,
        page_number=region.page_number,
        region_index=region.region_index,
        region_type=region.region_type,
        provider=analysis.provider,
        model=analysis.model,
        transcription=analysis.transcription,
        description=analysis.description,
        latex=analysis.latex,
        code=analysis.code,
        uncertainty=analysis.uncertainty,
        search_text=analysis.search_text,
        raw_response_json=analysis.raw_response_json,
        error_message=analysis.error_message,
    )
