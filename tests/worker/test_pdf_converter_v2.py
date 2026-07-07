import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from unittest.mock import patch

from PIL import Image
import fitz

from noteflow_worker.db.repository import LayoutBlock, VisualRegion, VlmResult
from noteflow_worker.pdf.artifacts import cleanup_orphaned_pdf_artifacts
from noteflow_worker.pdf.layout import (
    WorkingBlock,
    apply_vlm_formula_recovery,
    apply_multi_column_reading_order,
    classify_text_block,
    format_block_content,
    mark_layout_boilerplate,
)
from noteflow_worker.pdf.layout import build_layout_parse
from noteflow_worker.pdf.markdown import build_markdown_document, render_latex_blocks, render_visual_result
from noteflow_worker.pdf.math_normalizer import normalize_pdf_math_text
from noteflow_worker.pdf.ocr import make_ocr_backend
from noteflow_worker.pdf.parser import PageText, build_page_text_profile, classify_document_source, parse_pdf
from noteflow_worker.notes.providers import generations_with_retries
from noteflow_worker.pdf.markdown import build_markdown_page
from noteflow_worker.pdf.regions import (
    analyze_regions_with_vlm,
    build_visual_regions,
    create_full_page_region,
    flag_suspect_incomplete_transcription,
    native_formula_bboxes,
    region_input_fingerprint,
    select_formula_recovery_regions,
    select_regions_for_vlm,
)
from noteflow_worker.pdf.router import FULL_PAGE_VLM, HYBRID, NATIVE_TEXT, build_document_route_plan
from noteflow_worker.pdf.visual import VisualPage, analyze_pdf_visuals
from noteflow_worker.runtime.resource_pools import AcceleratorInfo, build_resource_pool_plan
from noteflow_worker.runtime.limits import process_resource_slot
from noteflow_worker.queue.redis_queue import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    PRIORITY_USER_VISIBLE,
    RedisTaskQueue,
    TaskPayload,
    priority_for_task_type,
)
from noteflow_worker.vision.providers import McpVisionProvider, RouterVisionProvider, VisionAnalysis, parse_api_keys


def visual_page(page: int, *, text: int, images: int = 0, coverage: float = 0.0, ocr: str | None = None):
    return VisualPage(page, f"/tmp/page-{page}.png", 1000, 1400, images, 0, coverage, text, ocr)


class ResourcePoolPlanTest(unittest.TestCase):
    def test_gpu_workers_are_derived_from_free_vram(self):
        plan = build_resource_pool_plan(
            cpu_count=16,
            accelerator=AcceleratorInfo("cuda", True, 1, 10 * 1024 * 1024 * 1024, "GPU"),
            gpu_memory_per_task_mib=2048,
            gpu_memory_reserve_mib=2048,
            gpu_worker_cap=8,
        )
        self.assertEqual(plan.cpu_workers, 2)
        self.assertEqual(plan.gpu_workers, 4)
        self.assertIn("free VRAM", plan.rationale["gpu"])

    def test_cpu_fallback_does_not_invent_gpu_workers(self):
        plan = build_resource_pool_plan(cpu_count=4, accelerator=AcceleratorInfo("cpu", False))
        self.assertEqual(plan.cpu_workers, 1)
        self.assertEqual(plan.gpu_workers, 0)

    def test_process_wide_slot_caps_concurrency_across_callers(self):
        active = 0
        peak = 0
        lock = Lock()

        def work():
            nonlocal active, peak
            with process_resource_slot("unit-test-global-limit", 2):
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.01)
                with lock:
                    active -= 1

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: work(), range(12)))
        self.assertEqual(peak, 2)

    def test_auto_ocr_selects_mps_backend_when_available(self):
        fake = type("FakeMpsOcr", (), {"name": "easyocr", "uses_gpu": True})()
        with patch("noteflow_worker.pdf.ocr.settings.pdf_ocr_backend", "auto"), patch(
            "noteflow_worker.pdf.ocr.EasyOcrBackend", return_value=fake
        ):
            backend = make_ocr_backend(AcceleratorInfo("mps", True, 1, None, "Apple Metal"))
        self.assertIs(backend, fake)


class PriorityQueueTest(unittest.TestCase):
    class FakeRedis:
        def __init__(self):
            self.lists = {}

        def rpush(self, name, payload):
            self.lists.setdefault(name, []).append(payload)

        def lpop(self, name):
            values = self.lists.get(name, [])
            return values.pop(0) if values else None

        def blpop(self, names, timeout=0):
            for name in names:
                value = self.lpop(name)
                if value is not None:
                    return name, value
            return None

    def make_queue(self):
        fake = self.FakeRedis()
        with patch("noteflow_worker.queue.redis_queue.redis.Redis.from_url", return_value=fake):
            queue = RedisTaskQueue()
        return queue, fake

    def test_task_type_priority_policy(self):
        self.assertEqual(priority_for_task_type("ASK_DOCUMENT"), PRIORITY_INTERACTIVE)
        self.assertEqual(priority_for_task_type("PARSE_DOCUMENT"), PRIORITY_USER_VISIBLE)
        self.assertEqual(priority_for_task_type("GENERATE_EMBEDDINGS"), PRIORITY_BACKGROUND)

    def test_weighted_priority_serves_background_without_starving_visible_work(self):
        queue, _ = self.make_queue()
        for index in range(8):
            queue.push(TaskPayload(f"high-{index}", "doc", "user", "ASK_DOCUMENT"))
        queue.push(TaskPayload("background", "doc", "user", "GENERATE_EMBEDDINGS"))
        popped = [queue.pop() for _ in range(4)]
        self.assertEqual(popped[0].resolved_priority, PRIORITY_INTERACTIVE)
        self.assertIn("background", {payload.task_id for payload in popped})

    def test_allowed_priorities_reserve_capacity_from_background_tasks(self):
        queue, _ = self.make_queue()
        queue.push(TaskPayload("background", "doc", "user", "GENERATE_EMBEDDINGS"))
        queue.push(TaskPayload("parse", "doc", "user", "PARSE_DOCUMENT"))
        payload = queue.pop((PRIORITY_INTERACTIVE, PRIORITY_USER_VISIBLE))
        self.assertEqual(payload.task_id, "parse")

    def test_legacy_background_payload_is_rehomed_instead_of_bypassing_reservation(self):
        queue, fake = self.make_queue()
        fake.rpush(
            queue.queue_name(PRIORITY_USER_VISIBLE).rsplit(":priority:", 1)[0],
            json.dumps({"taskId": "legacy-bg", "documentId": "doc", "userId": "user", "taskType": "GENERATE_EMBEDDINGS"}),
        )
        payload = queue.pop((PRIORITY_INTERACTIVE, PRIORITY_USER_VISIBLE))
        self.assertIsNone(payload)
        self.assertEqual(len(fake.lists[queue.queue_name(PRIORITY_BACKGROUND)]), 1)


class EvidenceRouterTest(unittest.TestCase):
    def test_document_source_uses_page_distribution_not_average_characters(self):
        profiles = [
            build_page_text_profile(PageText(1, "clear native text " * 30)),
            build_page_text_profile(PageText(2, "")),
        ]
        source, confidence, distribution = classify_document_source(profiles, "COURSE_NOTES")
        self.assertEqual(source, "MIXED")
        self.assertEqual(distribution, {"reliable": 1, "intermediate": 0, "weak": 1})
        self.assertGreater(confidence, 0.5)

    def test_user_document_type_and_page_evidence_drive_routes(self):
        profiles = [
            build_page_text_profile(PageText(1, "")),
            build_page_text_profile(PageText(2, "native lecture text " * 20)),
            build_page_text_profile(PageText(3, "plain prose " * 40)),
        ]
        visuals = [
            visual_page(1, text=0, images=1, coverage=0.8),
            visual_page(2, text=300, images=1, coverage=0.3),
            visual_page(3, text=500),
        ]
        plan = build_document_route_plan("LECTURE_SLIDES", profiles, visuals)
        self.assertEqual([page.mode for page in plan.pages], [FULL_PAGE_VLM, HYBRID, NATIVE_TEXT])
        handwritten = build_document_route_plan("HANDWRITTEN_NOTES", profiles, visuals)
        self.assertTrue(all(page.mode == FULL_PAGE_VLM and page.required_vlm for page in handwritten.pages))


class LayoutAndMarkdownQualityTest(unittest.TestCase):
    def _edge_block(self, page: int, content: str, block_type: str = "PARAGRAPH") -> WorkingBlock:
        return WorkingBlock(
            page, (5.0, 20.0, 0), block_type, content, [20, 5, 580, 30],
            None, [], None, 0.9, {"pageWidth": 600.0, "pageHeight": 800.0},
        )

    def test_noise_filter_requires_multiple_signals_and_protects_math_and_code(self):
        blocks = []
        for page in range(1, 13):
            blocks.extend(
                [
                    self._edge_block(page, "CS 246 Winter Term"),
                    self._edge_block(page, f"Page {page} of 12"),
                    self._edge_block(page, f"E[X_{page}] = {page}/2"),
                    self._edge_block(page, "return matrix[i][j];"),
                ]
            )
        marked = mark_layout_boilerplate(blocks)
        by_content = {block.content: block for block in marked}
        self.assertEqual(by_content["CS 246 Winter Term"].block_type, "BOILERPLATE")
        self.assertTrue(all(by_content[f"Page {page} of 12"].block_type == "BOILERPLATE" for page in range(1, 13)))
        self.assertTrue(all(by_content[f"E[X_{page}] = {page}/2"].block_type == "PARAGRAPH" for page in range(1, 13)))
        self.assertEqual(by_content["return matrix[i][j];"].block_type, "PARAGRAPH")
        self.assertTrue(by_content["return matrix[i][j];"].metadata["noiseAssessment"]["protected"])

    def test_math_font_boundaries_are_restored_without_splitting_math_identifiers(self):
        normalized = normalize_pdf_math_text("velocity 𝑣is given; 𝑡denotes time; 𝑎𝑛 converges")
        self.assertEqual(normalized, "velocity v is given; t denotes time; an converges")

    def test_unicode_math_transliterates_to_latex_safe_text(self):
        self.assertEqual(normalize_pdf_math_text("𝑦2 = 𝑓(𝑥2)"), "y2 = f(x2)")
        self.assertEqual(normalize_pdf_math_text("𝑞 ∈ Z, 𝑞 ̸= 0"), "q ∈ Z, q ≠ 0")
        self.assertEqual(normalize_pdf_math_text("𝜃 𝛼 𝚺 𝟕 𝐀"), "θ α Σ 7 A")
        self.assertEqual(normalize_pdf_math_text("𝑥 ̸∈ 𝑆"), "x ∉ S")

    def test_independent_vlm_formulas_render_as_separate_display_blocks(self):
        rendered = render_latex_blocks("$x=1$---FORMULA---$y=2$")
        self.assertEqual(rendered.count("$$"), 4)
        self.assertIn("$$\nx=1\n$$\n\n$$\ny=2\n$$", rendered)

    def test_vlm_tabular_math_does_not_create_nested_dollar_fences(self):
        rendered = render_latex_blocks(r"\\begin{tabular}{cc}$n$ & $r$\\\\1 & 2\\end{tabular}")
        self.assertIn(r"\\begin{array}{cc}n & r", rendered)
        self.assertNotIn("$n$", rendered)

    def test_multiline_native_formula_is_discovered_from_layout_evidence(self):
        class Page:
            rect = fitz.Rect(0, 0, 600, 800)

            def get_text(self, kind, sort=False):
                spans = [
                    ("lim", [300, 100, 325, 112]),
                    ("𝑛→∞", [296, 112, 330, 124]),
                    ("𝑛", [340, 92, 350, 104]),
                    ("𝑛+9=1", [338, 112, 390, 124]),
                ]
                return {"blocks": [{
                    "type": 0,
                    "bbox": [296, 92, 390, 124],
                    "lines": [
                        {"spans": [{"text": text, "bbox": bbox, "size": 10}]}
                        for text, bbox in spans
                    ],
                }]}

        self.assertEqual(native_formula_bboxes(Page()), [(296.0, 92.0, 390.0, 124.0)])

    def test_successful_formula_crop_replaces_only_overlapping_native_formula(self):
        native = WorkingBlock(
            1, (100.0, 300.0, 0), "FORMULA", "$$\nlim n infinity n n+9\n$$",
            [296, 92, 390, 124], None, [], None, 0.78, {"source": "pymupdf_text_block"},
        )
        prose = WorkingBlock(
            1, (80.0, 100.0, 1), "PARAGRAPH", "Use the definition.",
            [100, 80, 250, 91], None, [], None, 0.9, {"source": "pymupdf_text_block"},
        )
        region = VisualRegion(
            "doc", 1, 3, "FORMULA_IMAGE", "/tmp/formula.png",
            "[296,92,390,124]", "asset", 200, 80, 0.9,
        )
        result = VlmResult(
            "doc", 1, 3, "FORMULA_IMAGE", "gemini", "model", "", "",
            r"\\lim_{n\\to\\infty} \\frac{n}{n+9}=1", "", "", "limit",
            content_kind="formula",
        )
        recovered = apply_vlm_formula_recovery([prose, native], [result], [region])
        self.assertEqual(len(recovered), 2)
        self.assertEqual(recovered[0].content, "Use the definition.")
        self.assertIn(r"\\frac{n}{n+9}", recovered[1].content)
        self.assertEqual(recovered[1].metadata["source"], "vlm_formula_layout_recovery")

    def test_short_document_never_auto_deletes_repeated_edge_text(self):
        marked = mark_layout_boilerplate([self._edge_block(page, "Repeated edge note") for page in range(1, 6)])
        self.assertTrue(all(block.block_type == "PARAGRAPH" for block in marked))

    def test_two_column_blocks_read_left_column_before_right(self):
        def block(text, bbox, index):
            return WorkingBlock(1, (bbox[1], bbox[0], index), "PARAGRAPH", text, bbox, None, [], None, 0.8, {})

        blocks = [
            block("left top " + "word " * 10, [10, 100, 280, 140], 0),
            block("right top " + "word " * 10, [330, 100, 590, 140], 1),
            block("left bottom " + "word " * 10, [10, 200, 280, 240], 2),
            block("right bottom " + "word " * 10, [330, 200, 590, 240], 3),
        ]
        ordered = apply_multi_column_reading_order(blocks, 600)
        self.assertEqual(
            [item.content.split()[0:2] for item in ordered],
            [["left", "top"], ["left", "bottom"], ["right", "top"], ["right", "bottom"]],
        )

    def test_formula_image_keeps_latex_even_with_long_transcription(self):
        result = VlmResult(
            "doc", 1, 0, "IMAGE", "test", "model",
            "this is a long explanation containing more than twelve words about the displayed mathematical formula",
            "", r"\\int_0^1 x^2 dx = \\frac{1}{3}", "", "", "integration",
        )
        rendered = render_visual_result(result, "FORMULA_IMAGE")
        self.assertIn(r"\\int_0^1", rendered)
        self.assertIn("Formula transcription", rendered)

    def test_full_page_transcription_dedup_keeps_structured_latex_and_code(self):
        block = LayoutBlock(
            "doc", 1, 0, "PARAGRAPH", "Scanned derivation x equals one",
            metadata_json=json.dumps({"source": "page_level_vlm"}),
        )
        result = VlmResult(
            "doc", 1, 0, "FULL_PAGE_VISUAL", "fake", "v3",
            "Scanned derivation x equals one", "", r"x=1", "def f():\n    return 1",
            "symbol may be unclear", "derivation x one", content_kind="formula",
        )
        built = build_markdown_document("doc", [block], [result], document_type="HANDWRITTEN_NOTES")
        self.assertEqual(built.document.markdown.count("Scanned derivation x equals one"), 1)
        self.assertIn("$$\nx=1\n$$", built.document.markdown)
        self.assertIn("```python", built.document.markdown)
        self.assertIn("Transcription uncertainty", built.document.markdown)

    def test_multiline_formula_wins_over_table_heuristic_and_cases_are_balanced(self):
        lines = ["f(x) = x^2    x >= 0", "f(x) = -x    x < 0"]
        self.assertEqual(classify_text_block(lines), "FORMULA")
        rendered = format_block_content("\\begin{cases}\nx^2, x >= 0\n-x, x < 0", "FORMULA")
        self.assertIn("\\end{cases}", rendered)
        self.assertIn(r"x^2, x >= 0 \\", rendered)

    def test_complex_formula_environments_are_never_classified_as_code(self):
        formulas = [
            r"\begin{aligned}",
            r"A &= \begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}",
            r"\det(A) &= \prod_{i=1}^{n} \lambda_i",
            r"\end{aligned}",
        ]
        self.assertEqual(classify_text_block(formulas), "FORMULA")

    def test_multilanguage_code_blocks_remain_code(self):
        samples = [
            ["def solve(x):", "for item in x:", "return item"],
            ["#include <vector>", "int main() {", "return 0;", "}"],
            ["(define (square x)", "(let ([y (* x x)])", "y))"],
            ["SELECT id, title", "FROM documents", "WHERE status = 'READY';"],
            ["const square = (x) => x * x;", "console.log(square(4));"],
        ]
        for sample in samples:
            with self.subTest(sample=sample[0]):
                self.assertEqual(classify_text_block(sample), "CODE")
        prose = [
            "Next, let us consider the formal definition.",
            "function exists and is continuous on the interval.",
            "From the theorem, we obtain the result.",
        ]
        self.assertEqual(classify_text_block(prose), "PARAGRAPH")

    def test_code_fences_use_language_and_preserve_indentation(self):
        source = "def solve(items):\n    for item in items:\n        return item"
        rendered = format_block_content(source, "CODE")
        self.assertTrue(rendered.startswith("```python\n"))
        self.assertIn("\n    for item", rendered)
        self.assertIn("\n        return item", rendered)

        visual = VlmResult(
            "doc", 1, 0, "CODE_IMAGE", "fake", "v1", "", "", "",
            "const square = (x) => x * x;", "", "square function", content_kind="code",
        )
        self.assertTrue(render_visual_result(visual, "CODE_IMAGE").startswith("```javascript\n"))

    def test_ocr_fallback_and_rag_index_are_preserved(self):
        block = LayoutBlock(
            document_id="doc",
            page_number=1,
            block_index=0,
            block_type="MIXED_VISUAL",
            content="Important text recovered by local OCR",
            confidence=0.6,
            metadata_json=json.dumps({"ocrAvailable": True, "vlmStatus": "failed"}),
        )
        built = build_markdown_document("doc", [block], [])
        self.assertIn("Important text recovered", built.document.markdown)
        structure = json.loads(built.document.structure_json)
        quality = json.loads(built.document.quality_report_json)
        self.assertEqual(structure["schemaVersion"], "raw-markdown-index-v2")
        self.assertIn("visual_explanation", structure["pages"][0]["applicableScenarios"])
        self.assertTrue(quality["balancedMathFences"])
        self.assertIn("qualityGatePassed", quality)


class ResumeAndProviderTest(unittest.TestCase):
    def _region(self, root: Path, page: int) -> VisualRegion:
        image_path = root / f"region-{page}.png"
        Image.new("RGB", (32, 32), color=(page * 20, 10, 10)).save(image_path)
        return VisualRegion("doc", page, 0, "FULL_PAGE_VISUAL", str(image_path), None, None, 32, 32, 0.9)

    def test_successful_region_is_reused_and_only_new_region_is_persisted(self):
        class Provider:
            provider_name = "fake"
            model = "v1"
            calls = 0

            def analyze(self, image_path, region):
                self.calls += 1
                return VisionAnalysis("fake", "v1", transcription=f"page {region.page_number}")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = self._region(root, 1), self._region(root, 2)
            existing = VlmResult(
                "doc", 1, 0, "FULL_PAGE_VISUAL", "fake", "v1", "cached", "", "", "", "", "cached",
                input_fingerprint=region_input_fingerprint(first),
            )
            persisted = []
            provider = Provider()
            with patch("noteflow_worker.pdf.regions.make_vision_provider", return_value=provider):
                results = analyze_regions_with_vlm(
                    [first, second],
                    existing_results=[existing],
                    persist_result=persisted.append,
                    max_workers=2,
                )
            self.assertEqual(provider.calls, 1)
            self.assertEqual(results[0].transcription, "cached")
            self.assertEqual([item.page_number for item in persisted], [2])

    def test_required_regions_are_never_removed_by_optional_budget(self):
        pages = [visual_page(index, text=300, images=1, coverage=0.2) for index in range(1, 7)]
        regions = [
            VisualRegion("doc", index, 0, "IMAGE", f"/tmp/{index}.png", None, None, 10, 10, 0.8)
            for index in range(1, 7)
        ]
        with patch("noteflow_worker.pdf.regions.settings.vision_max_regions_per_document", 2):
            selected = select_regions_for_vlm(regions, pages, "COURSE_NOTES", required_region_keys={(6, 0)})
        self.assertIn((6, 0), {(region.page_number, region.region_index) for region in selected})
        self.assertEqual(len(selected), 3)

    def test_formula_budget_covers_pages_before_extra_regions(self):
        regions = [
            VisualRegion("doc", page, index, "FORMULA_IMAGE", f"/tmp/{page}-{index}.png", None, None, 80, 30 + index, 0.8)
            for page in range(1, 4)
            for index in range(2)
        ]
        selected = select_formula_recovery_regions(regions, 3)
        self.assertEqual({region.page_number for region in selected}, {1, 2, 3})

    def test_router_rotates_keys_and_fails_over(self):
        class Failing:
            provider_name = "a"
            model = "a"

            def analyze(self, image_path, region):
                return VisionAnalysis("a", "a", error_message="HTTP 429", uncertainty="rate limited")

        class Healthy:
            provider_name = "b"
            model = "b"

            def analyze(self, image_path, region):
                return VisionAnalysis("b", "b", transcription="ok")

        result = RouterVisionProvider([Failing(), Healthy()]).analyze("unused", object())
        self.assertEqual(result.provider, "b")
        self.assertEqual(parse_api_keys("a,b", "b\nc"), ["a", "b", "c"])

    def test_mcp_provider_initializes_session_and_calls_structured_tool(self):
        calls = []

        def fake_post(url, payload, headers=None, allow_empty=False):
            calls.append((payload, headers or {}))
            if payload.get("method") == "initialize":
                return {"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": "2025-11-25"}}, {"mcp-session-id": "session-1"}
            if payload.get("method") == "notifications/initialized":
                return {}, {}
            structured = {
                "transcription": "visible text", "description": "", "latex": "", "code": "",
                "uncertainty": "", "search_text": "visible text", "content_kind": "prose",
                "importance": "high", "reading_order": "top to bottom", "language": "en",
            }
            return {"jsonrpc": "2.0", "id": payload["id"], "result": {"structuredContent": structured}}, {}

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "region.png"
            Image.new("RGB", (10, 10), "white").save(image)
            region = VisualRegion("doc", 1, 0, "IMAGE", str(image), None, None, 10, 10, 0.8)
            with patch("noteflow_worker.vision.providers.settings.mcp_vision_endpoint", "http://mcp.test/mcp"), patch(
                "noteflow_worker.vision.providers.post_mcp_json", side_effect=fake_post
            ):
                result = McpVisionProvider("key").analyze(str(image), region)
        self.assertEqual(result.transcription, "visible text")
        self.assertEqual([call[0]["method"] for call in calls], ["initialize", "notifications/initialized", "tools/call"])
        self.assertEqual(calls[-1][1]["Mcp-Session-Id"], "session-1")


class ArtifactLifecycleTest(unittest.TestCase):
    def test_only_unreferenced_generated_files_are_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / "storage" / "uploads"
            upload.mkdir(parents=True)
            pdf = upload / "doc.pdf"
            pdf.write_bytes(b"pdf")
            rendered = root / "storage" / "rendered" / "doc"
            regions = root / "storage" / "regions" / "doc"
            rendered.mkdir(parents=True)
            regions.mkdir(parents=True)
            kept_page = rendered / "page-001.png"
            orphan_page = rendered / "old.png"
            kept_region = regions / "region.png"
            for path in (kept_page, orphan_page, kept_region):
                path.write_bytes(b"x")
            pages = [VisualPage(1, str(kept_page), 10, 10, 0, 0, 0.0, 10, None)]
            visual_regions = [VisualRegion("doc", 1, 0, "IMAGE", str(kept_region), None, None, 10, 10, 0.8)]
            removed = cleanup_orphaned_pdf_artifacts(str(pdf), "doc", pages, visual_regions)
            self.assertEqual(removed, [str(orphan_page)])
            self.assertTrue(kept_page.exists())
            self.assertTrue(kept_region.exists())


class FallbackAndCompletenessTest(unittest.TestCase):
    def test_full_page_region_reuses_page_render_without_duplicate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            render_path = root / "rendered" / "page-001.png"
            render_path.parent.mkdir(parents=True)
            Image.new("RGB", (64, 64), "white").save(render_path)
            output_dir = root / "regions"
            output_dir.mkdir()
            page = VisualPage(1, str(render_path), 64, 64, 0, 0, 0.0, 5, "handwritten " * 40)
            region = create_full_page_region(
                visual_page=page,
                document_id="doc",
                document_type="HANDWRITTEN_NOTES",
                output_dir=output_dir,
                page_asset_id="asset-1",
                source="page_router_full_page",
                region_type="HANDWRITTEN",
            )
            self.assertEqual(region.asset_path, str(render_path))
            self.assertEqual(list(output_dir.iterdir()), [])
            metadata = json.loads(region.metadata_json)
            self.assertTrue(metadata["reusesPageRender"])
            self.assertEqual(metadata["ocrTextLength"], len(page.ocr_text))

    def test_short_handwritten_transcription_is_flagged_against_ocr_baseline(self):
        region = VisualRegion(
            "doc", 1, 0, "HANDWRITTEN", "/tmp/page.png", None, None, 64, 64, 0.66,
            metadata_json=json.dumps({"ocrTextLength": 1000}),
        )
        short = flag_suspect_incomplete_transcription(
            region, VisionAnalysis("fake", "v1", transcription="only a few words")
        )
        self.assertIn("transcription_may_be_incomplete", short.uncertainty)
        complete = flag_suspect_incomplete_transcription(
            region, VisionAnalysis("fake", "v1", transcription="x" * 900)
        )
        self.assertNotIn("transcription_may_be_incomplete", complete.uncertainty)
        failed = flag_suspect_incomplete_transcription(
            region, VisionAnalysis("fake", "v1", error_message="HTTP 500")
        )
        self.assertNotIn("transcription_may_be_incomplete", failed.uncertainty or "")

    def test_incomplete_transcription_surfaces_as_page_warning(self):
        result = VlmResult(
            "doc", 1, 0, "HANDWRITTEN", "fake", "v1",
            "partial text", "", "", "", "transcription_may_be_incomplete: OCR read more", "partial text",
        )
        page = build_markdown_page("doc", 1, [], [result], document_type="HANDWRITTEN_NOTES")
        self.assertIn("handwritten_transcription_may_be_incomplete", json.loads(page.warnings_json))

    def test_notes_validation_errors_are_retried_as_stochastic(self):
        responses = [
            {"candidates": [{"content": {"parts": [{"text": json.dumps({"sections": []})}]}}]},
            {"candidates": [{"content": {"parts": [{"text": json.dumps({"sections": [{
                "heading": "H",
                "sectionType": "KEY_IDEAS",
                "markdown": "## H\ncontent",
                "confidence": 0.9,
                "warnings": [],
            }]})}]}}]},
        ]
        calls = {"count": 0}

        def request_fn():
            response = responses[calls["count"]]
            calls["count"] += 1
            return response

        with patch("noteflow_worker.notes.providers.settings.notes_request_max_attempts", 2), patch(
            "noteflow_worker.notes.providers.settings.notes_retry_backoff_seconds", 0.0
        ):
            generations = generations_with_retries("gemini", "test-model", request_fn)
        self.assertEqual(calls["count"], 2)
        self.assertIsNone(generations[0].error_message)
        self.assertEqual(generations[0].heading, "H")


class SyntheticPdfIntegrationTest(unittest.TestCase):
    def test_text_formula_columns_and_text_image_reach_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload = root / "storage" / "uploads"
            upload.mkdir(parents=True)
            pdf_path = upload / "synthetic.pdf"
            image_path = root / "important.png"
            image = Image.new("RGB", (600, 240), "white")
            from PIL import ImageDraw

            ImageDraw.Draw(image).text((30, 80), "IMPORTANT IMAGE TEXT 42", fill="black")
            image.save(image_path)

            document = fitz.open()
            page1 = document.new_page(width=600, height=800)
            page1.insert_text((40, 70), "Calculus Review", fontsize=20)
            page1.insert_text((40, 130), "f(x) = x^2 + 2x + 1", fontsize=13)
            page1.insert_textbox(fitz.Rect(40, 170, 560, 400), "This paragraph explains the formula. " * 12, fontsize=11)
            page2 = document.new_page(width=600, height=800)
            page2.insert_textbox(fitz.Rect(30, 80, 280, 340), "LEFT COLUMN FIRST\n" + "left material " * 25, fontsize=10)
            page2.insert_textbox(fitz.Rect(320, 80, 570, 340), "RIGHT COLUMN SECOND\n" + "right material " * 25, fontsize=10)
            page3 = document.new_page(width=600, height=800)
            page3.insert_image(fitz.Rect(60, 180, 540, 500), filename=str(image_path))
            document.save(pdf_path)
            document.close()

            parsed = parse_pdf(str(pdf_path), "COURSE_NOTES")
            plan = build_resource_pool_plan(
                cpu_count=2,
                configured_cpu_workers=1,
                accelerator=AcceleratorInfo("cpu", False),
            )

            class NoOcr:
                name = "disabled"
                uses_gpu = False

            with patch("noteflow_worker.pdf.visual.make_ocr_backend", return_value=NoOcr()):
                pages = analyze_pdf_visuals(str(pdf_path), "doc", plan)
            routes = build_document_route_plan("COURSE_NOTES", parsed.page_profiles, pages)
            self.assertEqual(routes.route_for_page(3).mode, FULL_PAGE_VLM)
            asset_ids = {1: "00000000-0000-0000-0000-000000000001", 2: "00000000-0000-0000-0000-000000000002", 3: "00000000-0000-0000-0000-000000000003"}
            regions = build_visual_regions(
                str(pdf_path),
                "doc",
                "COURSE_NOTES",
                pages,
                asset_ids,
                full_page_routes={3: "FULL_PAGE_VISUAL"},
            )
            self.assertTrue(any(region.page_number == 3 for region in regions))
            vlm = [
                VlmResult(
                    "doc", 3, 0, "FULL_PAGE_VISUAL", "fake", "v1",
                    "IMPORTANT IMAGE TEXT 42", "A text image", "", "", "", "important image text",
                )
            ]
            layout = build_layout_parse(
                str(pdf_path), "doc", pages, asset_ids, vlm,
                suppress_native_text_pages=routes.suppress_native_text_pages,
            )
            markdown = build_markdown_document("doc", layout.blocks, vlm).document.markdown
            self.assertIn("IMPORTANT IMAGE TEXT 42", markdown)
            self.assertIn("$$", markdown)
            self.assertLess(markdown.index("LEFT COLUMN FIRST"), markdown.index("RIGHT COLUMN SECOND"))


if __name__ == "__main__":
    unittest.main()
