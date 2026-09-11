from __future__ import annotations

from typing import Protocol

import httpx


class EmbeddingDimensionError(ValueError):
    pass


class EmbeddingRequestError(RuntimeError):
    pass


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class QwenEmbeddingClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/embeddings"):
            self.base_url = self.base_url[: -len("/embeddings")]
        self.api_key = api_key
        self.model = model
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
            if response.is_error:
                detail = response.text[:1000].replace("\n", " ")
                raise EmbeddingRequestError(
                    f"embedding provider returned HTTP {response.status_code}: {detail}"
                )
            payload = response.json()
        items = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in items]
        if len(vectors) != len(texts) or any(
            not isinstance(vector, list) or len(vector) != self.dimension for vector in vectors
        ):
            raise EmbeddingDimensionError(
                "embedding response must contain "
                f"{len(texts)} vectors of dimension {self.dimension}"
            )
        return vectors
