"""Apply per-user AI settings saved through the API onto the worker config.

The API stores user overrides in ``user_ai_settings`` (see the Spring
``AiSettings`` entity). The worker applies them onto the process-global
``settings`` object at task start, so every pipeline (notes, study, memory,
answer, vision, embeddings) picks them up through its normal fallback chain:
study/memory/answer already fall back to the notes provider and models.
"""

from noteflow_worker.config import settings
from noteflow_worker.db.repository import Repository


def apply_user_ai_settings(user_id: str) -> None:
    if not user_id:
        return
    try:
        row = _load_row(user_id)
    except Exception as exc:
        # The table only exists once the API has started at least once with
        # the settings feature. Missing table or transient DB issues must not
        # take the task down; environment configuration remains in effect.
        print(f"Skipping user AI settings ({exc.__class__.__name__}: {exc})")
        return
    if row is None:
        return

    gemini_key = (row.get("gemini_api_key") or "").strip()
    openai_key = (row.get("openai_api_key") or "").strip()
    if gemini_key:
        settings.gemini_api_key = gemini_key
    if openai_key:
        settings.openai_api_key = openai_key

    llm_provider = (row.get("llm_provider") or "").strip().lower()
    if llm_provider in ("gemini", "openai", "disabled"):
        settings.notes_provider = llm_provider
    # "auto"/empty keeps notes_provider blank so make_notes_provider resolves
    # by whichever API key is available.

    gemini_llm_model = (row.get("gemini_llm_model") or "").strip()
    if gemini_llm_model:
        settings.gemini_notes_model = gemini_llm_model
    openai_llm_model = (row.get("openai_llm_model") or "").strip()
    if openai_llm_model:
        settings.openai_notes_model = openai_llm_model

    embedding_provider = (row.get("embedding_provider") or "").strip().lower()
    if embedding_provider in ("gemini", "openai", "disabled"):
        settings.embedding_provider = embedding_provider
    elif embedding_provider == "auto":
        if settings.gemini_api_key:
            settings.embedding_provider = "gemini"
        elif settings.openai_api_key:
            settings.embedding_provider = "openai"

    gemini_embedding_model = (row.get("gemini_embedding_model") or "").strip()
    if gemini_embedding_model:
        settings.gemini_embedding_model = gemini_embedding_model
    openai_embedding_model = (row.get("openai_embedding_model") or "").strip()
    if openai_embedding_model:
        settings.openai_embedding_model = openai_embedding_model


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
