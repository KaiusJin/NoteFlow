import json
import hashlib
import math
import time
import random
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import fitz
from PIL import Image

from noteflow_worker.config import settings
from noteflow_worker.db.repository import VisualRegion, VlmResult
from noteflow_worker.pdf.layout import block_lines, classify_text_block, is_prose_dominant_block
from noteflow_worker.pdf.visual import VisualPage
from noteflow_worker.vision.providers import VisionAnalysis, make_vision_provider
from noteflow_worker.runtime.limits import process_resource_slot

MIN_REGION_AREA_RATIO = 0.03
FULL_PAGE_FALLBACK_MIN_IMAGE_COVERAGE = 0.12
LOW_NATIVE_TEXT_LENGTH = 160
LONG_DOCUMENT_PAGE_THRESHOLD = 120
CRITICAL_REGION_TYPES = {"CODE_IMAGE", "TEXT_IMAGE", "HANDWRITTEN", "FORMULA_IMAGE"}


def compute_image_hash(image: Image.Image) -> str:
    """Computes a simple Average Hash (aHash) for PIL image perceptual comparison."""
    try:
        img = image.convert('L').resize((8, 8), Image.Resampling.LANCZOS)
        get_pixels = getattr(img, "get_flattened_data", img.getdata)
        pixels = list(get_pixels())
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
    full_page_routes: dict[int, str] | None = None,
) -> list[VisualRegion]:
    full_page_routes = full_page_routes or {}
    visual_by_page = {page.page_number: page for page in visual_pages}
    output_dir = Path(pdf_path).parent.parent / "regions" / document_id
    output_dir.mkdir(parents=True, exist_ok=True)

    all_raw_regions: list[VisualRegion] = []
    with fitz.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            visual_page = visual_by_page.get(page_number)
            if not visual_page:
                continue
            forced_region_type = full_page_routes.get(page_number)
            if forced_region_type:
                all_raw_regions.append(
                    create_full_page_region(
                        visual_page=visual_page,
                        document_id=document_id,
                        document_type=document_type,
                        output_dir=output_dir,
                        page_asset_id=page_asset_ids.get(page_number),
                        source="page_router_full_page",
                        region_type=forced_region_type,
                    )
                )
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
            if image_hash and hash_counts[image_hash] >= repeat_threshold and can_drop_repeated_region(r):
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

    required_regions = [
        region for region in filtered_regions
        if region.region_type in {"HANDWRITTEN", "FULL_PAGE_VISUAL"}
        and json.loads(region.metadata_json or "{}").get("source") == "page_router_full_page"
    ]
    optional_regions = [region for region in filtered_regions if region not in required_regions]
    optional_regions.sort(key=lambda region: region_priority(region, visual_by_page.get(region.page_number)))
    # Discovery remains complete. VLM budgets are applied later so skipped calls
    # remain visible and auditable instead of disappearing from storage.
    return [*required_regions, *optional_regions]


def select_regions_for_vlm(
    regions: list[VisualRegion],
    visual_pages: list[VisualPage],
    document_type: str,
    required_region_keys: set[tuple[int, int]] | None = None,
) -> list[VisualRegion]:
    required_region_keys = required_region_keys or set()
    if document_type == "HANDWRITTEN_NOTES":
        return regions

    page_by_number = {page.page_number: page for page in visual_pages}
    required = [
        region for region in regions
        if (region.page_number, region.region_index) in required_region_keys
    ]
    optional = [
        region for region in regions
        if (region.page_number, region.region_index) not in required_region_keys
    ]
    # Native formula recovery regions are deliberately not forced through the
    # generic image budget. They are already gated by multi-line 2-D layout
    # evidence and exist specifically to repair a known lossy text-layer path.
    formula_recovery = [region for region in optional if region.region_type == "FORMULA_IMAGE"]
    formula_recovery = select_formula_recovery_regions(
        formula_recovery,
        settings.vision_formula_recovery_max_regions,
    )
    optional = [region for region in optional if region.region_type != "FORMULA_IMAGE"]
    if len(visual_pages) < LONG_DOCUMENT_PAGE_THRESHOLD:
        optional.sort(key=lambda region: region_priority(region, page_by_number.get(region.page_number)))
        return [*required, *formula_recovery, *optional[: settings.vision_max_regions_per_document]]

    def is_high_value(region: VisualRegion) -> bool:
        if region.region_type in CRITICAL_REGION_TYPES:
            return True
        page = page_by_number.get(region.page_number)
        if page is None:
            return False
        if page.text_length <= LOW_NATIVE_TEXT_LENGTH:
            return True
        if region.region_type == "DIAGRAM" and page.image_coverage >= FULL_PAGE_FALLBACK_MIN_IMAGE_COVERAGE:
            return True
        try:
            metadata = json.loads(region.metadata_json or "{}")
            if float(metadata.get("regionAreaRatio") or 0.0) >= 0.08:
                return True
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return False

    selected = [
        region for region in optional if is_high_value(region)
    ]
    selected.sort(key=lambda region: region_priority(region, page_by_number.get(region.page_number)))
    return [*required, *formula_recovery, *selected[: settings.vision_long_document_max_regions]]


def select_formula_recovery_regions(
    regions: list[VisualRegion],
    limit: int,
) -> list[VisualRegion]:
    """Bound formula calls while maximizing page coverage before extra detail."""
    if limit <= 0 or len(regions) <= limit:
        return sorted(regions, key=lambda region: (region.page_number, -region.height, region.region_index))
    by_page: dict[int, list[VisualRegion]] = {}
    for region in regions:
        by_page.setdefault(region.page_number, []).append(region)
    for page_regions in by_page.values():
        page_regions.sort(key=lambda region: (-region.height, -region.width, region.region_index))
    selected: list[VisualRegion] = []
    depth = 0
    while len(selected) < limit:
        added = False
        for page_number in sorted(by_page):
            page_regions = by_page[page_number]
            if depth < len(page_regions):
                selected.append(page_regions[depth])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        depth += 1
    return selected


def region_priority(region: VisualRegion, page: Optional[VisualPage]) -> tuple[int, int]:
    type_priority = {
        "HANDWRITTEN": 0,
        "FORMULA_IMAGE": 1,
        "CODE_IMAGE": 2,
        "TEXT_IMAGE": 3,
        "DIAGRAM": 4,
        "FULL_PAGE_VISUAL": 5,
        "IMAGE": 6,
    }.get(region.region_type, 5)
    low_text_priority = 0 if page and page.text_length <= LOW_NATIVE_TEXT_LENGTH else 1
    return (type_priority, low_text_priority, region.page_number)


def can_drop_repeated_region(region: VisualRegion) -> bool:
    if region.region_type in {"CODE_IMAGE", "FORMULA_IMAGE", "HANDWRITTEN", "FULL_PAGE_VISUAL"}:
        return False
    try:
        meta = json.loads(region.metadata_json or "{}")
        region_coverage = float(meta.get("regionAreaRatio") or 0.0)
    except Exception:
        region_coverage = 0.0
    return region_coverage < 0.08


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
    region_type: str | None = None,
) -> VisualRegion:
    with Image.open(visual_page.image_path) as page_image:
        page_width, page_height = page_image.size
        region_path = output_dir / f"page-{visual_page.page_number:03d}-region-full.png"
        page_image.save(region_path)
        image_hash = compute_image_hash(page_image)
    return VisualRegion(
        document_id=document_id,
        page_number=visual_page.page_number,
        region_index=0,
        region_type=region_type or classify_region_type(visual_page, document_type, 1.0),
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
                "nativeTextContext": visual_page.native_text_preview,
                "documentType": document_type,
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
    with Image.open(visual_page.image_path) as opened_page_image:
        page_image = opened_page_image.copy()
    page_width, page_height = page_image.size
    scale_x = page_width / max(float(page.rect.width), 1.0)
    scale_y = page_height / max(float(page.rect.height), 1.0)
    candidates = [
        (bbox, "embedded_image_block", None)
        for bbox in image_block_bboxes(page)
    ]
    candidates.extend(
        (bbox, "native_formula_layout_recovery", "FORMULA_IMAGE")
        for bbox in native_formula_bboxes(page)
    )
    regions: list[VisualRegion] = []

    for index, (bbox, source, forced_type) in enumerate(candidates):
        area_ratio = bbox_area_ratio(bbox, page.rect)
        if forced_type is None and area_ratio < MIN_REGION_AREA_RATIO:
            continue
        crop_box = bbox_to_pixels(bbox, scale_x, scale_y, page_width, page_height, padding=16)
        
        # Crop region
        crop = page_image.crop(crop_box)
        width, height = crop.size
        
        # Geometric filter
        if width < 40 or height < (24 if forced_type == "FORMULA_IMAGE" else 40):
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
                region_type=forced_type or classify_region_type(visual_page, document_type, area_ratio),
                asset_path=str(region_path),
                bbox_json=json.dumps([round(float(value), 2) for value in bbox], separators=(",", ":")),
                page_asset_id=page_asset_id,
                width=width,
                height=height,
                confidence=0.78,
                metadata_json=json.dumps(
                    {
                        "source": source,
                        "pageImagePath": visual_page.image_path,
                        "imageCoverage": visual_page.image_coverage,
                        "regionAreaRatio": area_ratio,
                        "imageHash": image_hash,
                        "nativeTextContext": visual_page.native_text_preview,
                        "documentType": document_type,
                    },
                    separators=(",", ":"),
                ),
            )
        )

    if not regions and visual_page.has_visual_content:
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
    page_image.close()
    return regions


def native_formula_bboxes(page) -> list[tuple[float, float, float, float]]:
    """Find native-text blocks whose 2-D math layout is lossy when linearized.

    This is deliberately based on semantic classification plus independent
    layout evidence (three or more visual lines), not fixed fonts, pages, or
    document templates. Single-line inline math remains on the fast native path.
    """
    seeds: list[tuple[float, float, float, float]] = []
    fragments: list[tuple[float, float, float, float]] = []
    page_dict = page.get_text("dict", sort=True)
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = block_lines(block)
        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        normalized_bbox = tuple(float(value) for value in bbox)
        block_type = classify_text_block(lines)
        if block_type == "FORMULA" or is_short_formula_fragment(lines):
            fragments.append(normalized_bbox)
        if len(lines) >= 3 and not is_prose_dominant_block(lines) and (
            block_type == "FORMULA" or looks_like_stacked_formula(lines)
        ):
            seeds.append(normalized_bbox)

    candidates = list(seeds)
    changed = True
    while changed:
        changed = False
        for fragment in fragments:
            if fragment in candidates:
                continue
            if any(same_formula_band(candidate, fragment, float(page.rect.width)) for candidate in candidates):
                candidates.append(fragment)
                changed = True
    return merge_formula_bboxes(candidates, float(page.rect.width))


def looks_like_stacked_formula(lines: list[str]) -> bool:
    joined = " ".join(lines)
    short_lines = sum(len(line.strip()) <= 28 for line in lines)
    math_evidence = bool(re.search(r"(?:^|\s)lim(?:\s|$)|[=<>≤≥→∞∑∫√^{}⎧⎨⎩]", joined))
    cases_evidence = bool(re.search(r"(^|\s)(if|otherwise)(\s|[,.]|$)", joined, re.IGNORECASE))
    prose_words = re.findall(r"[A-Za-z]{2,}", joined)
    return short_lines >= 2 and (
        (math_evidence and len(prose_words) <= 3)
        or (cases_evidence and len(prose_words) <= 8)
    )


def is_short_formula_fragment(lines: list[str]) -> bool:
    joined = " ".join(lines).strip()
    if not joined or len(lines) > 2 or len(joined) > 18:
        return False
    if len(re.findall(r"[A-Za-z]{2,}", joined)) > 1:
        return False
    return bool(re.search(r"[=<>≤≥→∞∑∫√]|[0-9𝑎-𝑧𝐴-𝑍α-ωΑ-Ω]", joined))


def merge_formula_bboxes(
    boxes: list[tuple[float, float, float, float]],
    page_width: float,
) -> list[tuple[float, float, float, float]]:
    """Merge fragments of one displayed equation without joining stacked equations."""
    merged: list[tuple[float, float, float, float]] = []
    for box in sorted(boxes, key=lambda value: (value[1], value[0])):
        target = next((index for index, current in enumerate(merged) if same_formula_band(current, box, page_width)), None)
        if target is None:
            merged.append(box)
            continue
        current = merged[target]
        merged[target] = (
            min(current[0], box[0]),
            min(current[1], box[1]),
            max(current[2], box[2]),
            max(current[3], box[3]),
        )
    return merged


def same_formula_band(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    page_width: float,
) -> bool:
    vertical_overlap = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    min_height = max(1.0, min(first[3] - first[1], second[3] - second[1]))
    horizontal_gap = max(0.0, max(first[0], second[0]) - min(first[2], second[2]))
    return vertical_overlap / min_height >= 0.20 and horizontal_gap <= max(12.0, page_width * 0.03)


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
    if area_ratio > 0.65:
        return "FULL_PAGE_VISUAL"
    if looks_like_code_ocr(visual_page.ocr_text or ""):
        return "CODE_IMAGE"
    if visual_page.drawing_count >= 8:
        return "DIAGRAM"
    if visual_page.text_length < 120 and visual_page.image_count > 0:
        return "TEXT_IMAGE"
    return "IMAGE"


def looks_like_code_ocr(text: str) -> bool:
    lowered = text.lower()
    code_markers = ("#include", "def ", "class ", "return ", "printf(", "malloc(", "public static", "=>")
    if any(marker in lowered for marker in code_markers):
        return True
    return sum(lowered.count(char) for char in "{}();=") >= 6


def analyze_regions_with_vlm(
    regions: list[VisualRegion],
    fail_on_error: bool = False,
    *,
    required_region_keys: set[tuple[int, int]] | None = None,
    existing_results: list[VlmResult] | None = None,
    persist_result=None,
    max_workers: int | None = None,
) -> list[VlmResult]:
    provider = make_vision_provider()
    required_region_keys = required_region_keys or set()
    existing_by_fingerprint = {
        result.input_fingerprint: result
        for result in existing_results or []
        if result.input_fingerprint and not result.error_message
    }
    results_by_key: dict[tuple[int, int], VlmResult] = {}
    pending: list[tuple[VisualRegion, str]] = []
    for region in regions:
        fingerprint = region_input_fingerprint(region)
        existing = existing_by_fingerprint.get(fingerprint)
        if existing:
            results_by_key[(region.page_number, region.region_index)] = VlmResult(
                **{
                    **existing.__dict__,
                    "page_number": region.page_number,
                    "region_index": region.region_index,
                    "region_type": region.region_type,
                }
            )
        else:
            pending.append((region, fingerprint))

    worker_count = max(1, min(max_workers or settings.vision_concurrent_requests, len(pending) or 1))
    batch_size = max(worker_count, settings.vision_batch_size)
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="vlm-region") as executor:
            future_map = {
                executor.submit(analyze_region_with_retries, provider, region): (region, fingerprint)
                for region, fingerprint in batch
            }
            for future in as_completed(future_map):
                region, fingerprint = future_map[future]
                analysis, attempts = future.result()
                result = to_vlm_result(region, analysis, fingerprint, attempts)
                results_by_key[(region.page_number, region.region_index)] = result
                if persist_result is not None:
                    persist_result(result)

    results = [results_by_key[(region.page_number, region.region_index)] for region in regions]
    if fail_on_error or required_region_keys:
        failures = [
            result for result in results
            if result.error_message
            and (fail_on_error or (result.page_number, result.region_index) in required_region_keys)
        ]
        if failures:
            details = "; ".join(
                f"page {result.page_number} region {result.region_index}: {result.error_message}"
                for result in failures[:5]
            )
            raise RuntimeError(f"Required VLM analysis failed after retries: {details}")
    return results


def analyze_region_with_retries(provider, region: VisualRegion) -> tuple[VisionAnalysis, int]:
    max_attempts = max(1, settings.vision_request_max_attempts)
    last_analysis: VisionAnalysis | None = None
    for attempt in range(1, max_attempts + 1):
        with process_resource_slot("vlm", settings.vision_concurrent_requests):
            analysis = provider.analyze(region.asset_path, region)
        if not analysis.error_message:
            return analysis, attempt
        last_analysis = analysis
        if attempt >= max_attempts or not is_retryable_vision_error(analysis.error_message):
            return analysis, attempt
        base = settings.vision_retry_backoff_seconds * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, min(1.0, settings.vision_retry_backoff_seconds * 0.25))
        time.sleep(min(settings.vision_retry_max_backoff_seconds, base + jitter))
    return last_analysis or provider.analyze(region.asset_path, region), max_attempts


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


def to_vlm_result(
    region: VisualRegion,
    analysis: VisionAnalysis,
    input_fingerprint: str | None = None,
    attempt_count: int = 1,
) -> VlmResult:
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
        input_fingerprint=input_fingerprint or region_input_fingerprint(region),
        attempt_count=attempt_count,
        content_kind=analysis.content_kind,
        importance=analysis.importance,
        reading_order=analysis.reading_order,
        language=analysis.language,
    )


def region_input_fingerprint(region: VisualRegion) -> str:
    digest = hashlib.sha256()
    digest.update(Path(region.asset_path).read_bytes())
    digest.update(region.region_type.encode("utf-8"))
    digest.update((region.bbox_json or "").encode("utf-8"))
    digest.update(b"vision-prompt-v4-formula-layout")
    return digest.hexdigest()
