from __future__ import annotations

# Explicit, user-set preferences. These are authoritative instructions and take
# precedence over learned USER_PREFERENCE memories, which are inferred and may
# be stale. Keys are a domain contract shared with the conversation API.
#
# Each key maps to either an allowed-value set (closed enum) or None (free
# text, bounded by MAX_PREFERENCE_VALUE_LENGTH).
PREFERENCE_KEYS: dict[str, set[str] | None] = {
    "ANSWER_LANGUAGE": None,
    "ANSWER_STYLE": None,
    "EXPLANATION_DEPTH": {"BRIEF", "STANDARD", "DETAILED"},
    "EXAMPLE_PREFERENCE": None,
    "DEFAULT_SEARCH_MODE": {"HYBRID", "SEMANTIC", "LEXICAL"},
    "LONG_TERM_MEMORY": {"ENABLED", "DISABLED"},
}

MAX_PREFERENCE_VALUE_LENGTH = 300

PREFERENCE_LONG_TERM_MEMORY = "LONG_TERM_MEMORY"
LONG_TERM_MEMORY_DISABLED = "DISABLED"


def validate_preference(key: str, value: str) -> str:
    """Validate and normalize one preference assignment; raises ValueError."""
    if key not in PREFERENCE_KEYS:
        allowed = ", ".join(sorted(PREFERENCE_KEYS))
        raise ValueError(f"Unknown preference key: {key}. Allowed keys: {allowed}")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"Preference {key} value must be a non-empty string.")
    if len(normalized) > MAX_PREFERENCE_VALUE_LENGTH:
        raise ValueError(f"Preference {key} value exceeds {MAX_PREFERENCE_VALUE_LENGTH} characters.")
    allowed_values = PREFERENCE_KEYS[key]
    if allowed_values is not None:
        canonical = normalized.upper()
        if canonical not in allowed_values:
            raise ValueError(f"Preference {key} must be one of: {', '.join(sorted(allowed_values))}")
        return canonical
    return normalized


def long_term_memory_enabled(preferences: dict[str, str]) -> bool:
    return preferences.get(PREFERENCE_LONG_TERM_MEMORY, "ENABLED") != LONG_TERM_MEMORY_DISABLED


def render_preferences_for_prompt(preferences: dict[str, str]) -> str:
    """Prompt-facing rendering of explicit settings.

    Rendered separately from learned memories so the prompt compiler can rank
    them above inferred user facts: explicit settings are authoritative.
    """
    visible = {key: value for key, value in preferences.items() if key != PREFERENCE_LONG_TERM_MEMORY}
    if not visible:
        return ""
    lines = ["User settings (explicitly configured; follow these over inferred preferences):"]
    lines.extend(f"- {key}: {value}" for key, value in sorted(visible.items()))
    return "\n".join(lines)
