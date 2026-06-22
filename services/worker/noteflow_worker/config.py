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
    vision_provider: str = "disabled"
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    openai_api_key: str = ""
    openai_vision_model: str = "gpt-4o-mini"
    vision_max_regions_per_document: int = 24
    vision_request_timeout_seconds: int = 60
    vision_request_max_attempts: int = 3
    vision_retry_backoff_seconds: float = 2.0


settings = Settings()
