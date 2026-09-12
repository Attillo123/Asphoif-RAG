from app.core.config import Settings


def test_chat_first_token_timeout_allows_two_minutes() -> None:
    settings = Settings(
        database_url="mysql+pymysql://u:p@localhost/rag",
        redis_url="redis://localhost:6379/0",
        opensearch_url="http://localhost:9200",
        openai_base_url="https://example.invalid/v1",
        openai_api_key="test",
        chat_model="chat-test",
        qwen_embedding_base_url="https://example.invalid/v1",
        qwen_embedding_api_key="test",
        qwen_embedding_model="embedding-test",
        chat_first_token_timeout_seconds=120,
    )
    assert settings.chat_first_token_timeout_seconds == 120
