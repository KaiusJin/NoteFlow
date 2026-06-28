from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[3] / ".env"),
        env_file_encoding="utf-8"
    )

    database_url: str = "postgresql://noteflow:noteflow@localhost:5432/noteflow"
    redis_url: str = "redis://localhost:6379/0"
    document_queue: str = "queue:document-analysis"
    block_timeout_seconds: int = 5
    worker_max_concurrent_tasks: int = 3
    worker_max_background_tasks: int = 1
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
    parse_stale_task_after_minutes: int = 10
    parse_max_task_retries: int = 3
    vision_provider: str = "disabled"
    vision_provider_order: str = "gemini,openai,mcp"
    vision_concurrent_requests: int = 4
    vision_batch_size: int = 4
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
    notes_group_target_tokens: int = 3200
    notes_group_max_tokens: int = 4500
    embedding_provider: str = "disabled"
    gemini_embedding_model: str = "gemini-embedding-001"
    openai_embedding_model: str = "text-embedding-3-small"
    local_embedding_model: str = "bge-small-en-v1.5"
    embedding_batch_size: int = 16
    embedding_max_concurrent_requests: int = 5


settings = Settings()
