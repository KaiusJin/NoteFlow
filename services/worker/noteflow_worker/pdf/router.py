from __future__ import annotations

from dataclasses import dataclass

from noteflow_worker.pdf.parser import PageTextProfile
from noteflow_worker.pdf.visual import VisualPage


NATIVE_TEXT = "NATIVE_TEXT"
HYBRID = "HYBRID"
FULL_PAGE_VLM = "FULL_PAGE_VLM"


@dataclass(frozen=True)
class PageRoute:
    page_number: int
    mode: str
    required_vlm: bool
    suppress_native_text: bool
    region_hint: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DocumentRoutePlan:
    document_type: str
    pages: list[PageRoute]

    @property
    def full_page_numbers(self) -> set[int]:
        return {page.page_number for page in self.pages if page.mode == FULL_PAGE_VLM}

    @property
    def required_vlm_keys(self) -> set[tuple[int, int]]:
        return {(page.page_number, 0) for page in self.pages if page.required_vlm}

    @property
    def suppress_native_text_pages(self) -> set[int]:
        return {page.page_number for page in self.pages if page.suppress_native_text}

    def route_for_page(self, page_number: int) -> PageRoute:
        return next(page for page in self.pages if page.page_number == page_number)


def build_document_route_plan(
    document_type: str | None,
    text_profiles: list[PageTextProfile],
    visual_pages: list[VisualPage],
) -> DocumentRoutePlan:
    normalized_type = document_type or "OTHER"
    text_by_page = {profile.page_number: profile for profile in text_profiles}
    visual_by_page = {page.page_number: page for page in visual_pages}
    page_numbers = sorted(set(text_by_page) | set(visual_by_page))
    pages = [
        route_page(normalized_type, text_by_page.get(number), visual_by_page.get(number))
        for number in page_numbers
    ]
    return DocumentRoutePlan(document_type=normalized_type, pages=pages)


def route_page(
    document_type: str,
    text: PageTextProfile | None,
    visual: VisualPage | None,
) -> PageRoute:
    page_number = text.page_number if text else visual.page_number if visual else 1
    text_length = text.native_text_length if text else 0
    text_quality = text.text_quality if text else 0.0
    has_visual = bool(visual and visual.has_visual_content)
    image_coverage = visual.image_coverage if visual else 0.0
    reasons: list[str] = []

    if document_type == "HANDWRITTEN_NOTES":
        return PageRoute(
            page_number,
            FULL_PAGE_VLM,
            True,
            True,
            "HANDWRITTEN",
            ("user_document_type=HANDWRITTEN_NOTES",),
        )

    if text_length < 24 and (has_visual or (visual and visual.ocr_text)):
        reasons.extend(("native_text_nearly_empty", "page_has_visual_evidence"))
        return PageRoute(page_number, FULL_PAGE_VLM, True, True, "FULL_PAGE_VISUAL", tuple(reasons))

    weak_text = text_length < 80 or text_quality < 0.38
    if weak_text and visual and visual.ocr_text and len(visual.ocr_text) > max(40, text_length * 2):
        reasons.extend(("weak_native_text", "ocr_contains_substantially_more_text"))
        return PageRoute(page_number, FULL_PAGE_VLM, True, True, "FULL_PAGE_VISUAL", tuple(reasons))

    if has_visual:
        reasons.append("page_has_images_or_dense_vector_drawings")
        if weak_text:
            reasons.append("native_text_is_weak_but_not_empty")
        if document_type == "LECTURE_SLIDES":
            reasons.append("slide_visuals_are_semantically_significant")
        return PageRoute(page_number, HYBRID, False, False, "AUTO", tuple(reasons))

    reasons.append("reliable_native_text" if not weak_text else "best_available_native_text")
    if image_coverage:
        reasons.append("subthreshold_image_coverage")
    return PageRoute(page_number, NATIVE_TEXT, False, False, "NONE", tuple(reasons))
