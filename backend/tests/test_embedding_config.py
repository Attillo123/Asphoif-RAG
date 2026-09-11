from app.core.config import Settings
from app.ingestion.embeddings import QwenEmbeddingClient


def test_qwen_embedding_endpoint_and_model_are_normalized() -> None:
    settings = Settings(
        _env_file=None,
        database_url="mysql+pymysql://user:pass@host/db",
        redis_url="redis://host:6379/0",
        opensearch_url="http://host:9200",
        openai_base_url="https://example.invalid/v1",
        openai_api_key="test",
        chat_model="chat-test",
        qwen_embedding_base_url="https://example.invalid/v1/embeddings",
        qwen_embedding_api_key="test",
        qwen_embedding_model="qwen3.7-text-embedding",
    )
    client = QwenEmbeddingClient(
        settings.qwen_embedding_base_url,
        settings.qwen_embedding_api_key.get_secret_value(),
        settings.qwen_embedding_model,
        settings.qwen_embedding_dimension,
    )

    assert client.base_url == "https://example.invalid/v1"
    assert settings.qwen_embedding_model == "qwen3.7-text-embedding"
