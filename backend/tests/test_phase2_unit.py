from __future__ import annotations

from sqlalchemy import DateTime
from sqlalchemy.dialects import mysql

from app.core.config import Settings
from app.models.knowledge import KnowledgeBase
from app.models.user import User
from app.services.auth import create_access_token
from app.services.knowledge import visible_documents, visible_knowledge_bases


def test_personal_knowledge_base_query_contains_owner_filter() -> None:
    user = User(id="user_personal", username="alice", role="personal")
    statement = visible_knowledge_bases(user)
    sql = str(statement.whereclause.compile(dialect=mysql.dialect()))
    assert "knowledge_bases.owner_id" in sql


def test_admin_knowledge_base_query_has_no_owner_filter() -> None:
    user = User(id="user_admin", username="admin", role="admin")
    statement = visible_knowledge_bases(user)
    sql = str(statement.whereclause.compile(dialect=mysql.dialect()))
    assert "knowledge_bases.owner_id" not in sql


def test_personal_document_query_contains_owner_filter() -> None:
    user = User(id="user_personal", username="alice", role="personal")
    statement = visible_documents(user, "kb_01")
    sql = str(statement.whereclause.compile(dialect=mysql.dialect()))
    assert "knowledge_bases.owner_id" in sql
    assert "documents.knowledge_base_id" in sql


def test_access_token_contains_user_identity_and_expiry() -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="mysql+pymysql://user:pass@host/db",
        redis_url="redis://host:6379/0",
        opensearch_url="http://host:9200",
        openai_base_url="https://example.invalid/v1",
        openai_api_key="test",
        chat_model="chat-test",
        qwen_embedding_base_url="https://example.invalid/v1",
        qwen_embedding_api_key="test",
        qwen_embedding_model="embedding-test",
        jwt_secret_key="test-secret-key",
    )
    token, expires_in = create_access_token(
        User(id="user_01", username="alice", role="personal"), settings
    )
    assert token
    assert expires_in == 1800


def test_knowledge_base_model_has_soft_delete_timestamp() -> None:
    assert isinstance(KnowledgeBase.__table__.c.deleted_at.type, DateTime)
