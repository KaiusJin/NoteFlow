from __future__ import annotations

import json

from noteflow_worker.config import settings
from noteflow_worker.memory.llm import StructuredMemoryLlm
from noteflow_worker.memory.models import ConversationMessage
from noteflow_worker.pdf.parser import estimate_tokens


SUMMARY_KEYS = {
    "topicsCovered",
    "userGoals",
    "establishedDefinitions",
    "unresolvedQuestions",
    "sourceDocumentsDiscussed",
    "importantMessageIds",
    "narrative",
}


def summary_response_schema() -> dict:
    list_of_strings = {"type": "ARRAY", "items": {"type": "STRING"}}
    return {
        "type": "OBJECT",
        "properties": {
            "topicsCovered": list_of_strings,
            "userGoals": list_of_strings,
            "establishedDefinitions": list_of_strings,
            "unresolvedQuestions": list_of_strings,
            "sourceDocumentsDiscussed": list_of_strings,
            "importantMessageIds": list_of_strings,
            "narrative": {"type": "STRING"},
        },
        "required": sorted(SUMMARY_KEYS),
    }


def build_rolling_summary(
    llm: StructuredMemoryLlm,
    previous_summary_json: str | None,
    evicted_messages: list[ConversationMessage],
) -> dict:
    """Fold evicted turns into the rolling summary.

    Incremental by construction: the model only ever sees the previous summary
    plus the newly evicted turns, so summarization cost stays proportional to
    new content instead of total conversation length.
    """
    if not evicted_messages:
        raise ValueError("Rolling summary requires at least one evicted message.")
    known_ids = {message.id for message in evicted_messages} | previous_important_ids(previous_summary_json)

    def validate(parsed: dict) -> None:
        validate_summary_payload(parsed, known_ids)

    prompt = build_summary_prompt(previous_summary_json, evicted_messages)
    parsed = llm.generate(prompt, summary_response_schema(), "noteflow_conversation_summary", validate)
    return normalize_summary(parsed, known_ids)


def previous_important_ids(previous_summary_json: str | None) -> set[str]:
    if not previous_summary_json:
        return set()
    try:
        parsed = json.loads(previous_summary_json)
    except json.JSONDecodeError:
        return set()
    ids = parsed.get("importantMessageIds") if isinstance(parsed, dict) else None
    return {str(item) for item in ids} if isinstance(ids, list) else set()


def validate_summary_payload(parsed: dict, known_message_ids: set[str]) -> None:
    if not isinstance(parsed, dict) or not SUMMARY_KEYS.issubset(parsed):
        raise ValueError("Summary response is missing required fields.")
    for key in SUMMARY_KEYS - {"narrative"}:
        value = parsed[key]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Summary field {key} must be an array of strings.")
    if not isinstance(parsed["narrative"], str) or not parsed["narrative"].strip():
        raise ValueError("Summary narrative must be a non-empty string.")
    invented = [item for item in parsed["importantMessageIds"] if item not in known_message_ids]
    if invented:
        raise ValueError(f"Summary referenced message ids that do not exist: {invented[:5]}")


def normalize_summary(parsed: dict, known_message_ids: set[str]) -> dict:
    normalized = {key: parsed[key] for key in SUMMARY_KEYS}
    normalized["importantMessageIds"] = [
        item for item in dict.fromkeys(normalized["importantMessageIds"]) if item in known_message_ids
    ]
    return normalized


def summary_text(summary: dict) -> str:
    """Render the structured summary into the prompt-facing text form."""
    sections = [
        ("Narrative", summary.get("narrative", "")),
        ("Topics covered", "; ".join(summary.get("topicsCovered", []))),
        ("User goals", "; ".join(summary.get("userGoals", []))),
        ("Established definitions", "; ".join(summary.get("establishedDefinitions", []))),
        ("Unresolved questions", "; ".join(summary.get("unresolvedQuestions", []))),
        ("Sources discussed", "; ".join(summary.get("sourceDocumentsDiscussed", []))),
    ]
    return "\n".join(f"{title}: {value}" for title, value in sections if value.strip())


def build_summary_prompt(previous_summary_json: str | None, evicted_messages: list[ConversationMessage]) -> str:
    transcript = "\n".join(
        f'<message id="{message.id}" role="{message.role}">\n{message.content}\n</message>'
        for message in evicted_messages
    )
    previous = previous_summary_json or "null"
    return f"""You maintain the rolling summary of a study conversation for the NoteFlow learning assistant.

Merge the previous summary state with the new conversation turns below into ONE updated summary.

Rules:
- Preserve still-relevant facts from the previous summary; drop resolved or obsolete items.
- The summary is conversational state, NOT academic evidence. Never present it as source material.
- importantMessageIds may only contain ids that literally appear in the input.
- Keep the narrative under {settings.memory_summary_max_tokens} tokens; be dense, factual, and neutral.
- Do not invent topics, goals, or definitions that are not grounded in the input.

Previous summary state (JSON or null):
{previous}

New conversation turns to fold in:
{transcript}
"""


def summary_token_count(text: str) -> int:
    return estimate_tokens(text)
