from __future__ import annotations

import json
from typing import Protocol

import httpx

from app.ingestion.chunking import ChunkDraft


class IndexingError(RuntimeError):
    pass


class ChunkIndexer(Protocol):
    async def upsert(
        self,
        *,
        chunk_ids: list[str],
        chunks: list[ChunkDraft],
        embeddings: list[list[float]],
        metadata: dict,
    ) -> None:
        ...

    async def delete_chunks(self, chunk_ids: list[str]) -> None:
        ...


class OpenSearchChunkIndexer:
    def __init__(
        self,
        base_url: str,
        write_alias: str,
        index_version: str,
        embedding_model: str,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.write_alias = write_alias
        self.index_version = index_version
        self.embedding_model = embedding_model
        self.auth = (username, password) if username and password else None
        self.verify_ssl = verify_ssl
        self.timeout_seconds = timeout_seconds

    async def upsert(
        self,
        *,
        chunk_ids: list[str],
        chunks: list[ChunkDraft],
        embeddings: list[list[float]],
        metadata: dict,
    ) -> None:
        if len(chunk_ids) != len(chunks) or len(chunks) != len(embeddings):
            raise IndexingError("chunk and embedding counts do not match")
        lines: list[str] = []
        for chunk_id, chunk, vector in zip(chunk_ids, chunks, embeddings, strict=True):
            lines.append(json.dumps({"index": {"_index": self.write_alias, "_id": chunk_id}}))
            lines.append(
                json.dumps(
                    {
                        "chunk_id": chunk_id,
                        "owner_id": metadata["owner_id"],
                        "knowledge_base_id": metadata["knowledge_base_id"],
                        "knowledge_base_version": metadata["knowledge_base_version"],
                        "document_id": metadata["document_id"],
                        "document_version": metadata["document_version"],
                        "title": (
                            chunk.section_path[-1]
                            if chunk.section_path
                            else metadata["file_name"]
                        ),
                        "content": chunk.content,
                        "content_vector": vector,
                        "embedding_model": self.embedding_model,
                        "index_version": self.index_version,
                        "metadata": {
                            "source_type": "upload",
                            "file_name": metadata["file_name"],
                            "section_path": " / ".join(chunk.section_path),
                            "language": chunk.language,
                        },
                    },
                    ensure_ascii=False,
                )
            )
        body = "\n".join(lines) + "\n"
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, verify=self.verify_ssl
        ) as client:
            response = await client.post(
                f"{self.base_url}/_bulk",
                # Prevent OpenSearch from auto-creating a physical index when
                # the write alias has not been initialized yet.
                params={"require_alias": "true"},
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
                auth=self.auth,
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("errors"):
            failed = [
                item
                for item in payload.get("items", [])
                if item.get("index", {}).get("error")
            ]
            raise IndexingError(f"OpenSearch bulk indexing failed for {len(failed)} chunks")

    async def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        lines = [
            json.dumps(
                {"delete": {"_index": self.write_alias, "_id": chunk_id}},
                ensure_ascii=False,
            )
            for chunk_id in chunk_ids
        ]
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds, verify=self.verify_ssl
        ) as client:
            response = await client.post(
                f"{self.base_url}/_bulk",
                # Deletions must also target the configured write alias.
                params={"require_alias": "true"},
                content=("\n".join(lines) + "\n").encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
                auth=self.auth,
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("errors"):
            failed = [
                item
                for item in payload.get("items", [])
                if item.get("delete", {}).get("error")
            ]
            raise IndexingError(f"OpenSearch bulk deletion failed for {len(failed)} chunks")
