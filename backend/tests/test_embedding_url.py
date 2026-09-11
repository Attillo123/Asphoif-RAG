from app.ingestion.embeddings import QwenEmbeddingClient


def test_embedding_client_does_not_duplicate_embeddings_path() -> None:
    client = QwenEmbeddingClient(
        "https://example.invalid/v1/embeddings",
        "test",
        "model",
        1024,
    )

    assert client.base_url == "https://example.invalid/v1"
