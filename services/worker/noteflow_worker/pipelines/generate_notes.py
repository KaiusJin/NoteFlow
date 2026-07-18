import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from noteflow_worker.config import settings
from noteflow_worker.db.repository import AiNoteSection, Repository, TextChunk
from noteflow_worker.notes.providers import NotesGeneration, make_notes_provider
from noteflow_worker.pdf.parser import estimate_tokens
from noteflow_worker.queue.redis_queue import TaskPayload


PROMPT_VERSION = "ai-notes-v1"
SECTION_INDEX_STRIDE = 1000


@dataclass(frozen=True)
class SourceGroup:
    index: int
    chunks: list[TextChunk]

    @property
    def page_start(self) -> int:
        return min((chunk.page_start or chunk.page_number) for chunk in self.chunks)

    @property
    def page_end(self) -> int:
        return max((chunk.page_end or chunk.page_number) for chunk in self.chunks)

    @property
    def chunk_indexes(self) -> list[int]:
        return [chunk.chunk_index for chunk in self.chunks]

    @property
    def chunk_ids(self) -> list[str]:
        return [chunk.id for chunk in self.chunks if chunk.id]


class GenerateNotesPipeline:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def run(self, payload: TaskPayload) -> None:
        note_id = ""
        sections: list[AiNoteSection] = []
        try:
            self._repository.mark_task_processing(payload.task_id, "GENERATING_NOTES", 10)
            self._repository.ensure_notes_schema()
            document = self._repository.load_document(payload.document_id)
            note_id = self._repository.latest_generating_note_id(payload.document_id)
            chunks = self._repository.load_chunks(payload.document_id)
            if not chunks:
                raise RuntimeError("Cannot generate notes because this document has no chunks.")

            provider = make_notes_provider()
            if provider.provider_name == "disabled":
                raise RuntimeError("Notes provider is not configured. Set NOTES_PROVIDER plus GEMINI_API_KEY or OPENAI_API_KEY.")

            groups = build_source_groups(chunks)
            if not groups:
                raise RuntimeError("Cannot generate notes because no source groups were produced.")

            sections = self._repository.load_ai_note_sections(note_id)
            completed_group_indexes = completed_source_group_indexes(sections)
            generations: list[NotesGeneration] = []
            pending_groups = [group for group in groups if group.index not in completed_group_indexes]
            failed_groups: dict[int, str] = {}
            if pending_groups:
                max_workers = max(1, settings.notes_max_concurrent_requests)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {
                        executor.submit(generate_group_sections, provider, document, group, len(groups)): group
                        for group in pending_groups
                    }
                    for future in as_completed(futures):
                        group = futures[future]
                        try:
                            group_generations = future.result()
                            group_sections: list[AiNoteSection] = []
                            group_section_count = len(group_generations)
                            for group_section_index, normalized in enumerate(group_generations):
                                generations.append(normalized)
                                section = to_note_section(
                                    note_id,
                                    payload.document_id,
                                    group,
                                    normalized,
                                    section_index_for_group(group.index, group_section_index),
                                    group_section_index,
                                    group_section_count,
                                )
                                self._repository.save_ai_note_section(section)
                                group_sections.append(section)
                            sections.extend(group_sections)
                            completed_group_indexes.add(group.index)
                        except Exception as exc:
                            failed_groups[group.index] = str(exc)
                        completed_count = len(completed_group_indexes)
                        progress = 15 + int((completed_count / max(1, len(groups))) * 70)
                        self._repository.mark_task_processing(payload.task_id, "GENERATING_NOTES", progress)
                        self._repository.update_ai_note_generation_progress(
                            note_id=note_id,
                            summary=build_progress_summary(completed_group_indexes, len(groups)),
                            provider=provider.provider_name,
                            model=provider.model,
                            prompt_version=PROMPT_VERSION,
                            quality_report_json=json.dumps(
                                build_quality_report(
                                    sections,
                                    [source_group for source_group in groups if source_group.index in completed_group_indexes],
                                    provider.provider_name,
                                    provider.model,
                                    total_source_group_count=len(groups),
                                    failed_source_group_indexes=sorted(failed_groups),
                                    error_message=first_error_message(failed_groups),
                                ),
                                separators=(",", ":"),
                            ),
                            metadata_json=json.dumps(
                                build_metadata(
                                    document,
                                    len(groups),
                                    completed_group_indexes,
                                    failed_source_group_indexes=sorted(failed_groups),
                                ),
                                separators=(",", ":"),
                            ),
                        )
            if failed_groups:
                first_failed_group_index = min(failed_groups)
                error_message = failed_groups[first_failed_group_index]
                self._repository.update_ai_note_generation_progress(
                    note_id=note_id,
                    summary=build_paused_summary(first_failed_group_index, len(groups), completed_group_indexes, error_message),
                    provider=provider.provider_name,
                    model=provider.model,
                    prompt_version=PROMPT_VERSION,
                    quality_report_json=json.dumps(
                        build_quality_report(
                            sections,
                            [source_group for source_group in groups if source_group.index in completed_group_indexes],
                            provider.provider_name,
                            provider.model,
                            total_source_group_count=len(groups),
                            failed_source_group_indexes=sorted(failed_groups),
                            error_message=error_message,
                        ),
                        separators=(",", ":"),
                    ),
                    metadata_json=json.dumps(
                        build_metadata(
                            document,
                            len(groups),
                            completed_group_indexes,
                            failed_source_group_indexes=sorted(failed_groups),
                        ),
                        separators=(",", ":"),
                    ),
                )
                raise RuntimeError(
                    f"AI notes generation paused with {len(failed_groups)} failed source group(s). "
                    f"First failed source group {first_failed_group_index + 1}: {error_message}"
                )

            sections = sort_note_sections(sections)
            markdown = assemble_note_markdown(document.title, sections)
            summary = build_section_summary(sections)
            quality_report = build_quality_report(
                sections,
                groups,
                provider.provider_name,
                provider.model,
                total_source_group_count=len(groups),
            )
            metadata = build_metadata(document, len(groups), completed_group_indexes)
            self._repository.save_ai_note(
                note_id=note_id,
                document_id=payload.document_id,
                markdown=markdown,
                summary=summary,
                provider=provider.provider_name,
                model=provider.model,
                prompt_version=PROMPT_VERSION,
                quality_report_json=json.dumps(quality_report, separators=(",", ":")),
                metadata_json=json.dumps(metadata, separators=(",", ":")),
                sections=sections,
            )
            self._repository.mark_task_completed(payload.task_id)
        except Exception as exc:
            if note_id and not sections:
                self._repository.fail_ai_note(note_id, str(exc))
            self._repository.mark_task_failed(payload.task_id, str(exc))
            raise


def build_source_groups(chunks: list[TextChunk]) -> list[SourceGroup]:
    groups: list[SourceGroup] = []
    current: list[TextChunk] = []
    current_tokens = 0
    for chunk in chunks:
        token_count = chunk.token_count or estimate_tokens(chunk.content)
        if current and current_tokens + token_count > settings.notes_group_max_tokens:
            groups.append(SourceGroup(index=len(groups), chunks=current))
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += token_count
        if current_tokens >= settings.notes_group_target_tokens:
            groups.append(SourceGroup(index=len(groups), chunks=current))
            current = []
            current_tokens = 0
    if current:
        groups.append(SourceGroup(index=len(groups), chunks=current))
    return groups


def completed_source_group_indexes(sections: list[AiNoteSection]) -> set[int]:
    group_counts: dict[int, int] = {}
    expected_counts: dict[int, int] = {}
    for section in sections:
        metadata = parse_json_object(section.metadata_json)
        group_index = metadata.get("sourceGroupIndex")
        section_count = metadata.get("sourceGroupSectionCount")
        if not isinstance(group_index, int) or not isinstance(section_count, int):
            continue
        group_counts[group_index] = group_counts.get(group_index, 0) + 1
        expected_counts[group_index] = max(expected_counts.get(group_index, 0), section_count)
    return {
        group_index
        for group_index, count in group_counts.items()
        if count >= expected_counts.get(group_index, 0) > 0
    }


def parse_json_object(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def generate_group_sections(provider, document, group: SourceGroup, group_count: int) -> list[NotesGeneration]:
    prompt = build_section_prompt(document, group, group_count)
    group_generations = provider.generate_sections(prompt)
    for generation in group_generations:
        if generation.error_message:
            raise RuntimeError(f"AI notes generation failed for source group {group.index + 1}: {generation.error_message}")
    normalized_generations: list[NotesGeneration] = []
    for generation in group_generations:
        normalized = normalize_generation(generation, group)
        validate_generation(normalized, group)
        normalized_generations.append(normalized)
    return normalized_generations


def section_index_for_group(group_index: int, group_section_index: int) -> int:
    return group_index * SECTION_INDEX_STRIDE + group_section_index


def first_error_message(failed_groups: dict[int, str]) -> str | None:
    if not failed_groups:
        return None
    return failed_groups[min(failed_groups)]


def sort_note_sections(sections: list[AiNoteSection]) -> list[AiNoteSection]:
    return sorted(sections, key=note_section_sort_key)


def note_section_sort_key(section: AiNoteSection) -> tuple:
    metadata = parse_json_object(section.metadata_json)
    group_index = metadata.get("sourceGroupIndex")
    group_section_index = metadata.get("sourceGroupSectionIndex")
    if not isinstance(group_index, int):
        group_index = section.section_index // SECTION_INDEX_STRIDE
    if not isinstance(group_section_index, int):
        group_section_index = section.section_index % SECTION_INDEX_STRIDE
    return (
        group_index,
        group_section_index,
        section.page_start if section.page_start is not None else 10**9,
        section.page_end if section.page_end is not None else 10**9,
        section.section_index,
        section.heading,
    )


def build_metadata(
    document,
    source_group_count: int,
    completed_group_indexes: set[int],
    failed_source_group_indexes: list[int] | None = None,
) -> dict:
    metadata = {
        "promptVersion": PROMPT_VERSION,
        "documentType": document.document_type,
        "contentSourceType": document.content_source_type,
        "sourceGroupCount": source_group_count,
        "completedSourceGroupCount": len(completed_group_indexes),
        "completedSourceGroupIndexes": sorted(completed_group_indexes),
        "aiOnly": True,
        "resumable": True,
    }
    if failed_source_group_indexes:
        metadata["failedSourceGroupIndexes"] = failed_source_group_indexes
    return metadata


def build_progress_summary(completed_group_indexes: set[int], source_group_count: int) -> str:
    return f"AI note generation in progress: completed {len(completed_group_indexes)}/{source_group_count} source groups."


def build_paused_summary(
    failed_group_index: int,
    source_group_count: int,
    completed_group_indexes: set[int],
    error_message: str,
) -> str:
    return (
        f"AI note generation paused at source group {failed_group_index + 1}/{source_group_count}. "
        f"Completed {len(completed_group_indexes)}/{source_group_count} groups. "
        f"Retry Generate AI Notes to continue from the failed group. Error: {error_message[:1000]}"
    )


def build_section_prompt(document, group: SourceGroup, group_count: int) -> str:
    source_chunks = "\n\n".join(format_source_chunk(chunk) for chunk in group.chunks)
    return f"""You are generating organized, comprehensive, source-grounded study notes for students using NoteFlow.

Use ONLY the provided source chunks. Do not use outside knowledge. Do not invent missing content.
The note content itself must be generated by you from the source chunks.
Write the generated notes entirely in English.

Document metadata:
- title: {document.title}
- document_type: {document.document_type}
- content_source_type: {document.content_source_type}
- total_pages: {document.page_count}
- source_group: {group.index + 1} of {group_count}
- page_range: {group.page_start}-{group.page_end}

Goal:
Generate clear, educational study notes covering ALL topics, concepts, theorems, examples, and details present in the source chunks. Do not skip or drop any topic or section from the source chunks.
To prevent information loss, you must split distinct topics, definitions, theorems, or examples into separate section objects in the "sections" JSON array.

Return ONLY valid JSON:
{{
  "sections": [
    {{
      "heading": "Clear heading for this specific section topic",
      "sectionType": "KEY_IDEAS | DEFINITION | THEOREM | FORMULA | EXAMPLE | PROOF | CODE_EXPLANATION | DIAGRAM_EXPLANATION | PITFALL | PAPER_SECTION | REVIEW_CHECKLIST",
      "markdown": "Markdown content for this specific section. Start with ## heading, followed by content, formulas, examples, etc. Do not combine multiple distinct topics into one markdown block if they can be separate sections.",
      "confidence": 0.0,
      "warnings": []
    }}
  ]
}}

Markdown Formatting Requirements:
- Write the notes entirely in ENGLISH.
- Cover all topics/sections found in the source chunks. Generate a separate section object in the array for each distinct topic/concept (e.g. loops, efficiency, classes should be distinct items in the JSON array). Do not discard any topics.
- Do NOT enforce any fixed subsection format (like '### Key Ideas' or '### Details'). Let the markdown section format and structure be determined dynamically and naturally by the actual document content.
- Present concepts, definitions, and theories clearly. Include step-by-step reasoning where applicable.
- For examples, format them clearly starting with 'E.g.' or 'Example:' or 'Worked Example:' (e.g. 'E.g.1', 'Example 2'). Show the problem statement and the step-by-step solution.
- Format all math formulas cleanly. Use LaTeX blocks $$ ... $$ for display equations and \\( ... \\) or $ ... $ for inline equations.
- Preserve all code blocks exactly in fenced code blocks with the appropriate language identifier.
- The system will attach the final traceable Sources subsection after your AI-generated note body.
- If math extraction appears corrupted, add warning "formula_may_be_corrupted".
- If the source is insufficient, say so and add warning "low_source_coverage".

Source chunks:
{source_chunks}
"""


def format_source_chunk(chunk: TextChunk) -> str:
    page_start = chunk.page_start or chunk.page_number
    page_end = chunk.page_end or page_start
    content = chunk.content.strip()
    return (
        f'<source_chunk id="{chunk.id}" index="{chunk.chunk_index}" '
        f'pages="{page_start}-{page_end}" type="{chunk.chunk_type}">\n'
        f"{content}\n"
        "</source_chunk>"
    )


def validate_generation(generation: NotesGeneration, group: SourceGroup) -> None:
    if not generation.markdown.strip():
        raise RuntimeError("AI returned an empty notes markdown section.")
    if "### Sources" not in generation.markdown:
        raise RuntimeError("AI notes section is missing a Sources subsection.")
    missing_indexes = [index for index in group.chunk_indexes if f"`{index}`" not in generation.markdown]
    if len(missing_indexes) == len(group.chunk_indexes):
        raise RuntimeError("AI notes section does not cite any source chunk indexes from its source group.")
    if generation.markdown.count("<source_chunk") > 0:
        raise RuntimeError("AI leaked raw source chunk tags into the notes output.")


def normalize_generation(generation: NotesGeneration, group: SourceGroup) -> NotesGeneration:
    markdown = generation.markdown.strip()
    warnings = list(generation.warnings or [])
    if "### Sources" not in markdown or not cites_any_source_index(markdown, group):
        markdown = strip_sources_section(markdown)
        markdown = "\n\n".join(
            part
            for part in [
                markdown,
                "### Sources",
                f"- Pages {format_page_range(group)}; chunks {format_chunk_indexes(group.chunk_indexes)}",
            ]
            if part.strip()
        )
        warnings.append("source_citations_attached_by_system")
    return NotesGeneration(
        provider=generation.provider,
        model=generation.model,
        heading=generation.heading,
        section_type=generation.section_type,
        markdown=markdown,
        confidence=generation.confidence,
        warnings=dedupe_preserve_order(warnings),
        raw_response_json=generation.raw_response_json,
        error_message=generation.error_message,
    )


def cites_any_source_index(markdown: str, group: SourceGroup) -> bool:
    return any(f"`{index}`" in markdown for index in group.chunk_indexes)


def strip_sources_section(markdown: str) -> str:
    return re.split(r"(?m)^###\s+Sources\s*$", markdown, maxsplit=1)[0].strip()


def format_page_range(group: SourceGroup) -> str:
    return str(group.page_start) if group.page_start == group.page_end else f"{group.page_start}-{group.page_end}"


def format_chunk_indexes(indexes: list[int]) -> str:
    return ", ".join(f"`{index}`" for index in indexes)


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def to_note_section(
    note_id: str,
    document_id: str,
    group: SourceGroup,
    generation: NotesGeneration,
    section_index: int,
    group_section_index: int,
    group_section_count: int,
) -> AiNoteSection:
    warnings = generation.warnings or []
    return AiNoteSection(
        note_id=note_id,
        document_id=document_id,
        section_index=section_index,
        section_type=sanitize_section_type(generation.section_type),
        heading=generation.heading.strip()[:500] or f"Source Group {group.index + 1}",
        markdown=generation.markdown.strip(),
        page_start=group.page_start,
        page_end=group.page_end,
        source_chunk_ids_json=json.dumps(group.chunk_ids, separators=(",", ":")),
        source_pages_json=json.dumps(list(range(group.page_start, group.page_end + 1)), separators=(",", ":")),
        confidence=max(0.0, min(1.0, generation.confidence)),
        warnings_json=json.dumps(warnings, separators=(",", ":")),
        metadata_json=json.dumps(
            {
                "rawResponse": generation.raw_response_json,
                "sourceChunkIndexes": group.chunk_indexes,
                "sourceGroupIndex": group.index,
                "sourceGroupSectionIndex": group_section_index,
                "sourceGroupSectionCount": group_section_count,
            },
            separators=(",", ":"),
        ),
    )


def sanitize_section_type(value: str) -> str:
    cleaned = re.sub(r"[^A-Z_]", "", (value or "KEY_IDEAS").upper())
    return cleaned or "KEY_IDEAS"


def assemble_note_markdown(title: str, sections: list[AiNoteSection]) -> str:
    sections = sort_note_sections(sections)
    coverage = format_sections_coverage(sections)
    parts = [
        f"# {title} - AI Notes",
        "## Overview",
        f"These notes were generated from all available parsed Markdown chunks{coverage} and cite their source pages and chunk indexes.",
        "## How To Study This Document",
        "- Start with the section headings to identify the main topics.",
        "- Review formulas and examples with the cited source pages open.",
        "- Use the Source Index to trace any generated note back to the original chunks.",
        "## Main Notes",
    ]
    parts.extend(section.markdown for section in sections)
    parts.append("## Source Index")
    for section in sections:
        pages = f"{section.page_start}-{section.page_end}" if section.page_start != section.page_end else str(section.page_start)
        metadata = json.loads(section.metadata_json or "{}")
        chunk_indexes = ", ".join(f"`{idx}`" for idx in metadata.get("sourceChunkIndexes", []))
        if not chunk_indexes:
            chunk_indexes = "stored source chunks"
        parts.append(f"- **{section.heading}**: pages {pages}; {chunk_indexes}")
    return "\n\n".join(part.strip() for part in parts if part.strip())


def format_sections_coverage(sections: list[AiNoteSection]) -> str:
    if not sections:
        return ""
    page_start = min(section.page_start for section in sections if section.page_start is not None)
    page_end = max(section.page_end for section in sections if section.page_end is not None)
    return f" covering pages {page_start}-{page_end}"


def build_summary(generations: list[NotesGeneration]) -> str:
    headings = [generation.heading for generation in generations if generation.heading]
    if not headings:
        return "AI-generated notes are ready."
    if len(headings) <= 8:
        return "AI-generated notes covering: " + "; ".join(headings)
    return "AI-generated notes covering " + str(len(headings)) + " sections, including: " + "; ".join(headings[:8]) + f"; and {len(headings) - 8} more."


def build_section_summary(sections: list[AiNoteSection]) -> str:
    sections = sort_note_sections(sections)
    headings = [section.heading for section in sections if section.heading]
    if not headings:
        return "AI-generated notes are ready."
    page_start = min((section.page_start for section in sections if section.page_start is not None), default=None)
    page_end = max((section.page_end for section in sections if section.page_end is not None), default=None)
    coverage = f" across pages {page_start}-{page_end}" if page_start is not None and page_end is not None else ""
    return f"AI-generated notes covering {len(headings)} sections{coverage}."


def build_quality_report(
    sections: list[AiNoteSection],
    completed_groups: list[SourceGroup],
    provider: str,
    model: str,
    total_source_group_count: int | None = None,
    failed_source_group_indexes: list[int] | None = None,
    error_message: str | None = None,
) -> dict:
    warnings: dict[str, int] = {}
    for section in sections:
        for warning in json.loads(section.warnings_json or "[]"):
            warnings[warning] = warnings.get(warning, 0) + 1
    report = {
        "sectionCount": len(sections),
        "sourceGroupCount": total_source_group_count if total_source_group_count is not None else len(completed_groups),
        "completedSourceGroupCount": len(completed_groups),
        "coveredPageStart": min((group.page_start for group in completed_groups), default=None),
        "coveredPageEnd": max((group.page_end for group in completed_groups), default=None),
        "coveredChunkCount": sum(len(group.chunks) for group in completed_groups),
        "averageConfidence": round(sum(section.confidence for section in sections) / len(sections), 3) if sections else 0.0,
        "warningCounts": warnings,
        "provider": provider,
        "model": model,
        "aiOnly": True,
        "resumable": True,
    }
    if failed_source_group_indexes:
        report["failedSourceGroupIndexes"] = failed_source_group_indexes
    if error_message:
        report["errorMessage"] = error_message[:1000]
    return report
