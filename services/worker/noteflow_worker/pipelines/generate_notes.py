import json
import re
from dataclasses import dataclass

from noteflow_worker.config import settings
from noteflow_worker.db.repository import AiNoteSection, Repository, TextChunk
from noteflow_worker.notes.providers import NotesGeneration, make_notes_provider
from noteflow_worker.pdf.parser import estimate_tokens
from noteflow_worker.queue.redis_queue import TaskPayload


PROMPT_VERSION = "ai-notes-v1"


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

            sections: list[AiNoteSection] = []
            generations: list[NotesGeneration] = []
            for group in groups:
                progress = 15 + int((group.index / max(1, len(groups))) * 70)
                self._repository.mark_task_processing(payload.task_id, "GENERATING_NOTES", progress)
                prompt = build_section_prompt(document, group, len(groups))
                generation = provider.generate_section(prompt)
                if generation.error_message:
                    raise RuntimeError(f"AI notes generation failed for source group {group.index + 1}: {generation.error_message}")
                generation = normalize_generation(generation, group)
                validate_generation(generation, group)
                generations.append(generation)
                sections.append(to_note_section(note_id, payload.document_id, group, generation))

            markdown = assemble_note_markdown(document.title, sections)
            summary = build_summary(generations)
            quality_report = build_quality_report(sections, groups, provider.provider_name, provider.model)
            metadata = {
                "promptVersion": PROMPT_VERSION,
                "documentType": document.document_type,
                "contentSourceType": document.content_source_type,
                "sourceGroupCount": len(groups),
                "aiOnly": True,
            }
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
            if note_id:
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

Return ONLY valid JSON:
{{
  "heading": "Summary heading of topics covered in this group",
  "sectionType": "KEY_IDEAS | DEFINITION | THEOREM | FORMULA | EXAMPLE | PROOF | CODE_EXPLANATION | DIAGRAM_EXPLANATION | PITFALL | PAPER_SECTION | REVIEW_CHECKLIST",
  "markdown": "Markdown section content containing all study notes. Start with a main heading or topic headings using ##, followed by content, examples, formulas, etc. as appropriate.",
  "confidence": 0.0,
  "warnings": []
}}

Markdown Formatting Requirements:
- Write the notes entirely in ENGLISH.
- Cover all topics/sections found in the source chunks. If the chunks contain multiple distinct topics (e.g. Loops, Efficiency, Classes), generate distinct ## headings for each topic in the single markdown output. Do not discard any topics.
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


def to_note_section(note_id: str, document_id: str, group: SourceGroup, generation: NotesGeneration) -> AiNoteSection:
    warnings = generation.warnings or []
    return AiNoteSection(
        note_id=note_id,
        document_id=document_id,
        section_index=group.index,
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
            },
            separators=(",", ":"),
        ),
    )


def sanitize_section_type(value: str) -> str:
    cleaned = re.sub(r"[^A-Z_]", "", (value or "KEY_IDEAS").upper())
    return cleaned or "KEY_IDEAS"


def assemble_note_markdown(title: str, sections: list[AiNoteSection]) -> str:
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


def build_quality_report(sections: list[AiNoteSection], groups: list[SourceGroup], provider: str, model: str) -> dict:
    warnings: dict[str, int] = {}
    for section in sections:
        for warning in json.loads(section.warnings_json or "[]"):
            warnings[warning] = warnings.get(warning, 0) + 1
    return {
        "sectionCount": len(sections),
        "sourceGroupCount": len(groups),
        "coveredPageStart": min((group.page_start for group in groups), default=None),
        "coveredPageEnd": max((group.page_end for group in groups), default=None),
        "coveredChunkCount": sum(len(group.chunks) for group in groups),
        "averageConfidence": round(sum(section.confidence for section in sections) / len(sections), 3) if sections else 0.0,
        "warningCounts": warnings,
        "provider": provider,
        "model": model,
        "aiOnly": True,
    }
