from __future__ import annotations

from dataclasses import dataclass

from noteflow_worker.memory.models import ConversationMessage
from noteflow_worker.pdf.parser import estimate_tokens


CLIP_MARKER = "\n[... truncated for context window ...]"


@dataclass(frozen=True)
class WindowSelection:
    window: list[ConversationMessage]
    token_count: int
    clipped_message_ids: list[str]
    excluded_message_count: int


def select_window(
    messages: list[ConversationMessage],
    *,
    max_tokens: int,
    min_turns: int,
    max_turns: int,
    message_max_tokens: int,
) -> WindowSelection:
    """Select the most recent turns that fit the token budget.

    Selection is newest-first so the turns closest to the current question are
    always favored. ``min_turns`` takes precedence over the token budget so a
    follow-up question never loses its immediate antecedent; overlong messages
    are clipped to ``message_max_tokens`` instead of silently dropped.
    """
    ordered = sorted(messages, key=lambda message: (message.created_at, message.id))
    selected: list[ConversationMessage] = []
    clipped_ids: list[str] = []
    total = 0
    for message in reversed(ordered):
        if len(selected) >= max_turns:
            break
        candidate, candidate_tokens, was_clipped = clip_message(message, message_max_tokens)
        within_budget = total + candidate_tokens <= max_tokens
        if not within_budget and len(selected) >= min_turns:
            break
        selected.append(candidate)
        total += candidate_tokens
        if was_clipped:
            clipped_ids.append(message.id)
    selected.reverse()
    return WindowSelection(
        window=selected,
        token_count=total,
        clipped_message_ids=clipped_ids,
        excluded_message_count=len(ordered) - len(selected),
    )


def clip_message(
    message: ConversationMessage,
    message_max_tokens: int,
) -> tuple[ConversationMessage, int, bool]:
    tokens = message.token_count or estimate_tokens(message.content)
    if tokens <= message_max_tokens:
        return message, tokens, False
    # estimate_tokens is monotone in length; a proportional cut converges in
    # one pass and avoids re-tokenization loops.
    keep_chars = max(1, int(len(message.content) * (message_max_tokens / max(1, tokens))))
    clipped_content = message.content[:keep_chars].rstrip() + CLIP_MARKER
    clipped = ConversationMessage(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=clipped_content,
        token_count=estimate_tokens(clipped_content),
        created_at=message.created_at,
        status=message.status,
        metadata_json=message.metadata_json,
    )
    return clipped, clipped.token_count, True


def should_compress(unsummarized_tokens: int, trigger_tokens: int) -> bool:
    """High-water mark check; compression runs only when the backlog is large."""
    return unsummarized_tokens > max(1, trigger_tokens)


def split_for_compression(
    messages: list[ConversationMessage],
    *,
    retain_tokens: int,
) -> tuple[list[ConversationMessage], list[ConversationMessage]]:
    """Split unsummarized history into (evict, retain).

    The newest ``retain_tokens`` worth of messages stay verbatim (low-water
    mark); everything older is folded into the rolling summary. Evicting down
    to the low-water mark instead of the trigger point provides hysteresis so
    maintenance does not run again on the very next turn.
    """
    ordered = sorted(messages, key=lambda message: (message.created_at, message.id))
    retained: list[ConversationMessage] = []
    total = 0
    boundary = len(ordered)
    for index in range(len(ordered) - 1, -1, -1):
        message = ordered[index]
        tokens = message.token_count or estimate_tokens(message.content)
        if total + tokens > retain_tokens and retained:
            break
        retained.insert(0, message)
        total += tokens
        boundary = index
    evicted = ordered[:boundary]
    return evicted, retained
