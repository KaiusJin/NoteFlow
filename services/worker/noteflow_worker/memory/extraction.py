from __future__ import annotations

from noteflow_worker.config import settings
from noteflow_worker.memory.llm import StructuredMemoryLlm
from noteflow_worker.memory.models import MEMORY_TYPES, ConversationMessage, MemoryCandidate


def extraction_response_schema() -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "memories": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "memoryType": {"type": "STRING", "enum": sorted(MEMORY_TYPES)},
                        "content": {"type": "STRING"},
                        "confidence": {"type": "NUMBER"},
                        "sourceMessageId": {"type": "STRING"},
                        "ttlDays": {"type": "INTEGER"},
                    },
                    "required": ["memoryType", "content", "confidence", "sourceMessageId", "ttlDays"],
                },
            }
        },
        "required": ["memories"],
    }


def extract_memory_candidates(
    llm: StructuredMemoryLlm,
    messages: list[ConversationMessage],
    active_summary_text: str | None,
) -> list[MemoryCandidate]:
    """Extract durable user facts from new turns.

    Returns an empty list when nothing is worth remembering; extraction is not
    forced to produce output. Every candidate is validated against the type
    whitelist, confidence bounds, and the set of real message ids.
    """
    if not messages:
        return []
    known_ids = {message.id for message in messages}

    def validate(parsed: dict) -> None:
        validate_extraction_payload(parsed, known_ids)

    prompt = build_extraction_prompt(messages, active_summary_text)
    parsed = llm.generate(prompt, extraction_response_schema(), "noteflow_memory_extraction", validate)
    candidates = []
    for item in parsed["memories"]:
        confidence = float(item["confidence"])
        if confidence < settings.memory_extraction_min_confidence:
            continue
        ttl_days = int(item["ttlDays"])
        candidates.append(
            MemoryCandidate(
                memory_type=item["memoryType"],
                content=item["content"].strip(),
                confidence=min(1.0, confidence),
                source_message_id=item["sourceMessageId"] or None,
                ttl_days=ttl_days if ttl_days > 0 else None,
            )
        )
    return candidates


def validate_extraction_payload(parsed: dict, known_message_ids: set[str]) -> None:
    if not isinstance(parsed, dict) or "memories" not in parsed:
        raise ValueError("Extraction response must contain a memories array.")
    memories = parsed["memories"]
    if not isinstance(memories, list):
        raise ValueError("Extraction memories must be an array.")
    for index, item in enumerate(memories):
        if not isinstance(item, dict):
            raise ValueError(f"Extraction memory {index} must be an object.")
        if item.get("memoryType") not in MEMORY_TYPES:
            raise ValueError(f"Extraction memory {index} has a disallowed memoryType.")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"Extraction memory {index} content must be a non-empty string.")
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError(f"Extraction memory {index} confidence must be between 0 and 1.")
        source_id = item.get("sourceMessageId")
        if not isinstance(source_id, str):
            raise ValueError(f"Extraction memory {index} sourceMessageId must be a string.")
        if source_id and source_id not in known_message_ids:
            raise ValueError(f"Extraction memory {index} referenced a message id that does not exist.")
        if not isinstance(item.get("ttlDays"), int):
            raise ValueError(f"Extraction memory {index} ttlDays must be an integer.")


def build_extraction_prompt(messages: list[ConversationMessage], active_summary_text: str | None) -> str:
    transcript = "\n".join(
        f'<message id="{message.id}" role="{message.role}">\n{message.content}\n</message>'
        for message in messages
    )
    summary_block = active_summary_text or "(none)"
    type_list = ", ".join(sorted(MEMORY_TYPES))
    return f"""You extract durable long-term memories about the student from a NoteFlow study conversation.

Return ONLY facts worth remembering across future sessions. An empty memories array is a correct answer when nothing qualifies.

Allowed memoryType values: {type_list}

Positive examples:
- USER_PREFERENCE: "Prefers worked examples before formal definitions."
- KNOWN_DIFFICULTY: "Struggles with geometric distribution problems."
- LEARNING_GOAL: "Preparing for the STAT 230 midterm."
- COURSE_CONTEXT: "Enrolled in MATH 239 this term."
- EXPLICIT_FACT: something the user explicitly asked to remember.

Hard constraints:
- NEVER record inferred sensitive traits (health, ethnicity, religion, politics, sexuality, disability, immigration status, precise location). If a candidate would reveal these, omit it entirely.
- Do not store transient conversation state (what was just asked) or general academic facts; those belong to retrieval, not user memory.
- Each memory must be one self-contained sentence, understandable without this conversation.
- sourceMessageId must be the id of the message that best evidences the memory, chosen from the ids in the input.
- confidence reflects how directly the user stated it: explicit statements near 1.0, weak inferences below 0.5.
- ttlDays: 0 means no expiry; use a positive value only for time-bound facts (e.g. an exam date passes).

Conversation summary so far (context only, do not re-extract from it):
{summary_block}

New conversation turns:
{transcript}
"""
