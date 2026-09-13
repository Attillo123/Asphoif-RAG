from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "asphoif-rag"
    app_env: Literal["dev", "test", "prod"] = "dev"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    database_url: str
    redis_url: str
    local_file_root: Path = PROJECT_ROOT / "data" / "files"

    opensearch_url: str
    opensearch_username: str | None = None
    opensearch_password: SecretStr | None = None
    opensearch_verify_ssl: bool = True
    opensearch_index: str = "rag_chunks_v1"
    opensearch_read_alias: str = "rag_chunks_read"
    opensearch_write_alias: str = "rag_chunks_write"
    retrieval_version: str = "app-hybrid-v1"
    dense_k: int = Field(default=5, ge=1, le=100)
    sparse_k: int = Field(default=5, ge=1, le=100)
    final_top_k: int = Field(default=5, ge=1, le=100)
    retrieval_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    chat_first_token_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    chat_idle_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    chat_total_timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    chat_context_max_chars: int = Field(default=12000, ge=1000, le=100000)
    prompt_version: str = "prompt-v1"
    embedding_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    retrieval_cache_ttl_seconds: int = Field(default=1800, ge=30, le=86400)
    embedding_cache_ttl_seconds: int = Field(default=172800, ge=300, le=604800)
    circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    circuit_failure_window_seconds: float = Field(default=30.0, gt=0, le=300)
    circuit_open_seconds: float = Field(default=15.0, gt=0, le=600)
    circuit_half_open_max_calls: int = Field(default=2, ge=1, le=10)

    openai_base_url: str
    openai_api_key: SecretStr
    chat_model: str
    qwen_embedding_base_url: str
    qwen_embedding_api_key: SecretStr
    qwen_embedding_model: str
    qwen_embedding_dimension: int = Field(default=1024, ge=256, le=2560)
    rerank_model: str | None = None
    evaluation_judge_base_url: str | None = None
    evaluation_judge_api_key: SecretStr | None = None
    evaluation_judge_model: str | None = None
    evaluation_judge_prompt_version: str = "ragas-default-v1"

    healthcheck_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)

    jwt_secret_key: SecretStr = SecretStr("development-only-change-me")
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)

    ingestion_max_file_size_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    ingestion_chunk_size: int = Field(default=1000, ge=100, le=10000)
    ingestion_chunk_overlap: int = Field(default=150, ge=0, le=2000)
    ingestion_max_attempts: int = Field(default=3, ge=1, le=10)
    ingestion_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    ingestion_worker_id: str = Field(default="local-ingestion-worker", min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> Settings:
        if self.ingestion_chunk_overlap >= self.ingestion_chunk_size:
            raise ValueError("INGESTION_CHUNK_OVERLAP must be smaller than INGESTION_CHUNK_SIZE")
        return self

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("qwen_embedding_dimension")
    @classmethod
    def validate_initial_embedding_dimension(cls, value: int) -> int:
        if value != 1024:
            raise ValueError(
                "QWEN_EMBEDDING_DIMENSION must be 1024 for the initial OpenSearch mapping"
            )
        return value

    @model_validator(mode="after")
    def validate_production_jwt_secret(self) -> Settings:
        secret = self.jwt_secret_key.get_secret_value()
        if self.app_env == "prod" and (
            secret == "development-only-change-me" or len(secret) < 32
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be a random value of at least 32 characters in prod"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
