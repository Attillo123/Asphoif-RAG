from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.errors import ErrorCode
from app.db.session import _async_database_url
from app.main import create_app


def test_mysql_url_is_adapted_for_async_driver() -> None:
    assert _async_database_url("mysql+pymysql://user:pass@host/db") == (
        "mysql+aiomysql://user:pass@host/db"
    )
    assert _async_database_url("mysql+aiomysql://user:pass@host/db") == (
        "mysql+aiomysql://user:pass@host/db"
    )


def test_embedding_dimension_is_frozen_at_1024() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="mysql+pymysql://user:pass@host/db",
            redis_url="redis://host:6379/0",
            opensearch_url="http://host:9200",
            openai_base_url="https://example.invalid/v1",
            openai_api_key=SecretStr("test"),
            chat_model="chat-test",
            qwen_embedding_base_url="https://example.invalid/v1",
            qwen_embedding_api_key=SecretStr("test"),
            qwen_embedding_model="embedding-test",
            qwen_embedding_dimension=1536,
        )


def test_live_endpoint_returns_unified_response() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="mysql+pymysql://user:pass@host/db",
        redis_url="redis://host:6379/0",
        local_file_root=Path("data/files"),
        opensearch_url="http://host:9200",
        openai_base_url="https://example.invalid/v1",
        openai_api_key=SecretStr("test"),
        chat_model="chat-test",
        qwen_embedding_base_url="https://example.invalid/v1",
        qwen_embedding_api_key=SecretStr("test"),
        qwen_embedding_model="embedding-test",
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "req_test"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req_test"
    assert response.json() == {
        "success": True,
        "code": 0,
        "message": "ok",
        "data": {"status": "alive"},
        "error": None,
        "request_id": "req_test",
        "timestamp": response.json()["timestamp"],
    }


def test_dependency_error_code_is_in_reserved_range() -> None:
    assert 90000 <= ErrorCode.DEPENDENCY_UNAVAILABLE <= 90999
