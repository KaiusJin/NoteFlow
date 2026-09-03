import threading

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8"
    )

    database_url: str = "postgresql://noteflow:noteflow@localhost:5432/noteflow"
    # Keep the scale-to-zero worker inside Supabase's connection budget. Each
    # spawned parse process is additionally capped to one connection.
    db_pool_min_size: int = 0
    db_pool_max_size: int = 4
    db_pool_acquire_timeout_seconds: float = 30.0
    redis_url: str = "redis://localhost:6379/0"
    noteflow_api_url: str = "http://localhost:8080"
    noteflow_api_timeout_seconds: float = 15.0
    noteflow_internal_token: str = ""
    document_queue: str = "queue:document-analysis"
    block_timeout_seconds: int = 5
    queue_lease_seconds: int = 1800
    queue_reclaim_batch_size: int = 100
    # Empty queues are probed at most once per priority, then the consumer
    # backs off. These defaults remain comfortably inside Upstash's free
    # monthly command budget when the product is idle.
    queue_idle_poll_seconds: float = 5.0
    queue_reclaim_interval_seconds: float = 30.0
    # Unexpected consumer crashes (exceptions escaping a pipeline) re-enqueue
    # the payload this many times before it goes to the dead-letter queue.
    queue_max_redeliveries: int = 3
    worker_max_concurrent_tasks: int = 4
    worker_max_background_tasks: int = 2
    # PARSE_DOCUMENT is CPU-bound (MuPDF/OCR/layout) and starves the GIL when it
    # shares the thread pool with I/O-bound pipelines. Route it to a spawn-based
    # process pool instead. 0 keeps the old single-pool thread behaviour.
    worker_parse_process_workers: int = 2
    worker_metrics_bind_address: str = "127.0.0.1"
    worker_metrics_port: int = 9464
    otel_exporter_otlp_endpoint: str = ""
    pdf_cpu_workers: int = 0
    pdf_io_workers: int = 0
    pdf_gpu_workers: int = 0
    pdf_gpu_memory_per_task_mib: int = 2048
    pdf_gpu_memory_reserve_mib: int = 1536
    pdf_gpu_worker_cap: int = 4
    pdf_ocr_backend: str = "auto"
    pdf_ocr_languages: str = "en"
    pdf_enable_gpu_ocr: bool = True
    pdf_cleanup_intermediate_files: bool = True
    pdf_sandbox_max_open_files: int = 256
    pdf_sandbox_max_output_mib: int = 1024
    parse_stale_task_after_minutes: int = 10
    parse_max_task_retries: int = 3
    embedding_stale_task_after_minutes: int = 10
    embedding_max_task_retries: int = 3
    # How often the main loop re-runs stale task recovery while running
    # (startup always runs it once). 0 disables periodic recovery.
    recovery_interval_seconds: int = 600
    # Time to let in-flight tasks finish after SIGTERM/SIGINT before cancelling.
    worker_shutdown_drain_timeout_seconds: int = 120
    # Maximum number of tasks handled by a Cloud Run Job invocation. 0 keeps
    # the long-running local worker behaviour.
    worker_max_tasks_per_run: int = 0
    task_execution_stale_without_lease_minutes: int = 35
    vision_provider: str = "disabled"
    vision_provider_order: str = "gemini,openai,mcp"
    vision_concurrent_requests: int = 4
    gemini_api_key: str = ""
    gemini_api_keys: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    openai_api_keys: str = ""
    openai_vision_model: str = "gpt-4o-mini"
    mcp_vision_endpoint: str = ""
    mcp_vision_api_key: str = ""
    mcp_vision_api_keys: str = ""
    mcp_vision_tool: str = "analyze_pdf_region"
    mcp_protocol_version: str = "2025-11-25"
    vision_max_regions_per_document: int = 96
    vision_long_document_max_regions: int = 64
    vision_formula_recovery_max_regions: int = 320
    vision_request_timeout_seconds: int = 60
    vision_request_max_attempts: int = 3
    vision_retry_backoff_seconds: float = 2.0
    vision_retry_max_backoff_seconds: float = 30.0
    notes_provider: str = ""
    gemini_notes_model: str = "gemini-2.5-flash"
    openai_notes_model: str = "gpt-4o-mini"
    notes_request_timeout_seconds: int = 120
    notes_request_max_attempts: int = 3
    notes_retry_backoff_seconds: float = 2.0
    notes_max_concurrent_requests: int = 3
    notes_stale_task_after_minutes: int = 10
    # Hard cap on stale-recovery requeues for GENERATE_NOTES so a poison task
    # cannot be retried forever on every worker restart.
    notes_max_task_retries: int = 3
    notes_max_output_tokens: int = 8192
    # Gemini 2.5 thinking-token budget for notes generation. 0 disables thinking
    # so the full output budget serves the JSON; a negative value omits the
    # field entirely (use the model default).
    notes_gemini_thinking_budget: int = 0
    notes_group_target_tokens: int = 3200
    notes_group_max_tokens: int = 4500
    embedding_provider: str = "disabled"
    gemini_embedding_model: str = "gemini-embedding-001"
    openai_embedding_model: str = "text-embedding-3-small"
    # 0 derives the pgvector column dimension from the configured OpenAI model;
    # set explicitly (1536/3072) when using a model outside the known mapping.
    openai_embedding_dimensions: int = 0
    local_embedding_model: str = "bge-small-en-v1.5"
    embedding_batch_size: int = 16
    embedding_max_concurrent_requests: int = 5

    # Conversation memory: short-term window
    memory_window_max_tokens: int = 2400
    memory_window_min_turns: int = 2
    memory_window_max_turns: int = 12
    memory_window_message_max_tokens: int = 900

    # Conversation memory: rolling summary compression (high/low water marks)
    memory_summary_trigger_tokens: int = 3200
    memory_summary_retain_tokens: int = 1400
    memory_summary_max_tokens: int = 700

    # Conversation memory: long-term recall
    memory_recall_limit: int = 5
    memory_recall_candidate_limit: int = 24
    memory_recall_min_similarity: float = 0.55
    memory_recall_similarity_weight: float = 0.60
    memory_recall_recency_weight: float = 0.20
    memory_recall_confidence_weight: float = 0.20
    memory_recall_recency_half_life_days: float = 14.0
    memory_recall_max_tokens: int = 600
    memory_recall_fallback_limit: int = 3

    # Conversation memory: extraction and consolidation
    memory_extraction_min_new_tokens: int = 120
    memory_extraction_max_messages: int = 40
    memory_extraction_min_confidence: float = 0.5
    memory_dedup_similarity_threshold: float = 0.90
    memory_update_similarity_threshold: float = 0.78
    memory_max_active_per_user: int = 400

    # Conversation memory: LLM provider for summarization/extraction.
    # Empty values fall back to the notes provider selection and models.
    memory_llm_provider: str = ""
    memory_gemini_model: str = ""
    memory_openai_model: str = ""
    memory_request_timeout_seconds: int = 60
    memory_request_max_attempts: int = 3
    memory_retry_backoff_seconds: float = 2.0

    # Conversation memory: maintenance execution
    memory_maintenance_inline: bool = False
    memory_maintenance_stale_after_minutes: int = 10
    memory_window_fetch_limit: int = 96
    memory_maintenance_fetch_limit: int = 200

    # Study modules: shared LLM (empty falls back to the notes provider/models)
    study_llm_provider: str = ""
    study_gemini_model: str = ""
    study_openai_model: str = ""
    study_request_timeout_seconds: int = 120
    study_request_max_attempts: int = 3
    study_retry_backoff_seconds: float = 2.0
    study_stale_task_after_minutes: int = 10
    study_global_max_concurrent_requests: int = 6
    study_max_output_tokens: int = 8192
    # Gemini 2.5 thinking-token budget for study extraction. 0 disables thinking
    # so the full output budget serves the JSON; a negative value omits the
    # field entirely (use the model default).
    study_gemini_thinking_budget: int = 0
    study_lease_seconds: int = 1800

    # Flashcard generation
    flashcards_max_concurrent_requests: int = 3
    flashcards_group_target_tokens: int = 2400
    flashcards_group_max_tokens: int = 3600
    flashcards_max_per_document: int = 300
    flashcards_min_confidence: float = 0.5
    flashcards_dedup_similarity_threshold: float = 0.86
    flashcards_per_1000_source_tokens: float = 3.0

    # Quiz generation
    quiz_max_concurrent_requests: int = 3
    quiz_group_target_tokens: int = 2600
    quiz_group_max_tokens: int = 3800
    quiz_max_questions_per_document: int = 120
    quiz_default_difficulty_mix: str = "EASY:0.3,MEDIUM:0.5,HARD:0.2"
    quiz_min_confidence: float = 0.5
    quiz_dedup_similarity_threshold: float = 0.86
    quiz_questions_per_1000_source_tokens: float = 1.5

    # Quiz grading
    quiz_grading_max_concurrent_requests: int = 3
    quiz_free_text_pass_threshold: float = 0.6

    # Spaced repetition (SM-2)
    srs_initial_ease: float = 2.5
    srs_min_ease: float = 1.3
    srs_first_interval_days: int = 1
    srs_second_interval_days: int = 6

    # Conversation answering: LLM (empty falls back to notes provider/models)
    answer_llm_provider: str = ""
    answer_gemini_model: str = ""
    answer_openai_model: str = ""
    answer_request_timeout_seconds: int = 90
    answer_request_max_attempts: int = 3
    answer_retry_backoff_seconds: float = 2.0

    # Conversation answering: evidence retrieval budgets
    answer_evidence_top_k: int = 8
    answer_evidence_candidate_limit: int = 32
    answer_evidence_min_similarity: float = 0.30
    answer_evidence_max_tokens: int = 2800
    answer_evidence_item_max_tokens: int = 700
    answer_stale_task_after_minutes: int = 5
    # Hard cap on stale-recovery requeues for answer/agent tasks.
    answer_max_task_retries: int = 3

    # Tool-calling conversation agent: bounded ReAct loop over the same
    # structured-output LLM and retrieval contracts as normal answering.
    agent_max_steps: int = 12
    agent_wall_timeout_seconds: int = 90
    agent_token_budget: int = 60000
    agent_input_cost_per_million_tokens: float = 0.0
    agent_output_cost_per_million_tokens: float = 0.0
    agent_max_reflections: int = 2
    agent_trace_observation_max_chars: int = 1400
    agent_full_observation_steps: int = 3


settings = Settings()


# Per-task AI settings overrides (user_ai_settings applied by
# user_settings.apply_user_ai_settings). These live in thread-local storage
# instead of being written onto the global ``settings`` object: the main loop
# runs up to ``worker_max_concurrent_tasks`` pipelines in parallel, and a
# global write from one user's task would leak its API keys/models into
# another user's concurrent task. Subprocesses never see these overrides;
# parse-side vision configuration stays environment-driven by design.
_task_ai_overrides = threading.local()

# Fields a per-user settings row may override. Everything else in Settings is
# deployment configuration and cannot be changed from the database.
TASK_OVERRIDABLE_AI_FIELDS = (
    "gemini_api_key",
    "openai_api_key",
    "notes_provider",
    "gemini_notes_model",
    "openai_notes_model",
    "embedding_provider",
    "gemini_embedding_model",
    "openai_embedding_model",
)


def set_task_ai_overrides(values: dict[str, str]) -> None:
    _task_ai_overrides.values = {k: v for k, v in values.items() if v}


def clear_task_ai_overrides() -> None:
    _task_ai_overrides.values = None


def ai_setting(name: str) -> str:
    """Read a user-overridable AI setting for the current task thread."""
    values = getattr(_task_ai_overrides, "values", None)
    if values and name in values:
        return values[name]
    return getattr(settings, name)
