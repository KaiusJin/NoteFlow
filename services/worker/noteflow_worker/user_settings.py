"""Apply per-user AI settings saved through the API onto the current task.

The API stores user overrides in ``user_ai_settings`` (see the Spring
``AiSettings`` entity). The worker applies them as a **thread-local task
snapshot** (see ``config.set_task_ai_overrides``), so every pipeline (notes,
study, memory, answer, vision, embeddings) picks them up through the
``config.ai_setting`` accessor at provider-construction time.

Writing to the process-global ``settings`` object is not safe: the main loop
runs several pipelines in parallel and a global write from one user's task
would leak its API keys/models into another user's concurrent task.
``main._process_payload`` clears the snapshot in a ``finally`` block so pooled
threads never carry one user's settings into the next task.
"""

import logging

from noteflow_worker.config import TASK_OVERRIDABLE_AI_FIELDS, ai_setting, clear_task_ai_overrides, set_task_ai_overrides
from noteflow_worker.db.repository import Repository

logger = logging.getLogger(__name__)


def apply_user_ai_settings(user_id: str) -> None:
    clear_task_ai_overrides()
    if not user_id:
        return
    try:
        row = _load_row(user_id)
    except Exception as exc:
        # The table only exists once the API has started at least once with
        # the settings feature. Missing table or transient DB issues must not
        # take the task down; environment configuration remains in effect.
        logger.warning("user_ai_settings_unavailable error_type=%s error=%s", exc.__class__.__name__, exc)
        return
    if row is None:
        return

    gemini_key = (row.get("gemini_api_key") or "").strip()
    openai_key = (row.get("openai_api_key") or "").strip()

    llm_provider = (row.get("llm_provider") or "").strip().lower()
    # "auto"/empty keeps notes_provider blank so make_notes_provider resolves
    # by whichever API key is available.
    if llm_provider not in ("gemini", "openai", "disabled"):
        llm_provider = ""

    embedding_provider = (row.get("embedding_provider") or "").strip().lower()
    if embedding_provider not in ("gemini", "openai", "disabled"):
        if embedding_provider == "auto":
            if gemini_key:
                embedding_provider = "gemini"
            elif openai_key:
                embedding_provider = "openai"
            # Fall back to whichever key the environment provides, matching the
            # previous global-resolution behaviour for auto rows without keys.
            elif ai_setting("gemini_api_key"):
                embedding_provider = "gemini"
            elif ai_setting("openai_api_key"):
                embedding_provider = "openai"
        else:
            embedding_provider = ""

    overrides = {
        "gemini_api_key": gemini_key,
        "openai_api_key": openai_key,
        "notes_provider": llm_provider,
        "gemini_notes_model": (row.get("gemini_llm_model") or "").strip(),
        "openai_notes_model": (row.get("openai_llm_model") or "").strip(),
        "embedding_provider": embedding_provider,
        "gemini_embedding_model": (row.get("gemini_embedding_model") or "").strip(),
        "openai_embedding_model": (row.get("openai_embedding_model") or "").strip(),
    }
    unexpected = set(overrides) - set(TASK_OVERRIDABLE_AI_FIELDS)
    if unexpected:  # defensive: keeps the thread-local contract explicit
        raise ValueError(f"unexpected task AI override fields: {sorted(unexpected)}")
    set_task_ai_overrides(overrides)


def _load_row(user_id: str):
    with Repository().connect() as conn:
        return conn.execute(
            """
            SELECT gemini_api_key, openai_api_key,
                   llm_provider, gemini_llm_model, openai_llm_model,
                   embedding_provider, gemini_embedding_model, openai_embedding_model
            FROM user_ai_settings
            WHERE user_id = %s
            """,
            (user_id,),
        ).fetchone()
