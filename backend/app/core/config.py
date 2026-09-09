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

    openai_base_url: str
    openai_api_key: SecretStr
    chat_model: str
    qwen_embedding_base_url: str
    qwen_embedding_api_key: SecretStr
    qwen_embedding_model: str
    qwen_embedding_dimension: int = Field(default=1024, ge=256, le=2560)
    rerank_model: str | None = None

    healthcheck_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)

    jwt_secret_key: SecretStr = SecretStr("development-only-change-me")
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)

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
