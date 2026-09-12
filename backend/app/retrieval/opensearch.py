from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


class OpenSearchRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def build_alias_actions(
    *, index_name: str, alias: str, existing_indices: list[str]
) -> list[dict[str, Any]]:
    """Build idempotent actions that leave an alias pointing at index_name."""
    actions = [
        {"remove": {"index": index, "alias": alias}}
        for index in existing_indices
        if index != index_name
    ]
    if index_name not in existing_indices:
        actions.append({"add": {"index": index_name, "alias": alias}})
    return actions


def index_definition(dimension: int = 1024) -> dict[str, Any]:
    return {
        "settings": {
            "index.knn": True,
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {
                "analyzer": {
                    "rag_index_analyzer": {"type": "custom", "tokenizer": "ik_max_word"},
                    "rag_search_analyzer": {"type": "custom", "tokenizer": "ik_smart"},
                }
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "chunk_id": {"type": "keyword"},
                "owner_id": {"type": "keyword"},
                "knowledge_base_id": {"type": "keyword"},
                "knowledge_base_version": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "document_version": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "analyzer": "rag_index_analyzer",
                    "search_analyzer": "rag_search_analyzer",
                },
                "content": {
                    "type": "text",
                    "analyzer": "rag_index_analyzer",
                    "search_analyzer": "rag_search_analyzer",
                },
                "content_vector": {
                    "type": "knn_vector",
                    "dimension": dimension,
                    "method": {
                        "name": "hnsw",
                        "engine": "lucene",
                        "space_type": "cosinesimil",
                    },
                },
                "embedding_model": {"type": "keyword"},
                "index_version": {"type": "keyword"},
                "metadata": {
                    "type": "object",
                    "dynamic": "strict",
                    "properties": {
                        "source_type": {"type": "keyword"},
                        "file_name": {"type": "keyword", "ignore_above": 256},
                        "section_path": {"type": "keyword", "ignore_above": 512},
                        "tags": {"type": "keyword", "ignore_above": 256},
                        "language": {"type": "keyword"},
                    },
                },
            },
        },
    }


class _OpenSearchHTTP:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
        timeout_seconds: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = (username, password) if username and password else None
        self.verify_ssl = verify_ssl
        self.timeout_seconds = timeout_seconds

    async def request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                auth=self.auth,
                verify=self.verify_ssl,
                timeout=self.timeout_seconds,
            ) as client:
                response = await client.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise OpenSearchRequestError("OpenSearch request timed out") from exc
        except httpx.HTTPError as exc:
            raise OpenSearchRequestError(
                f"OpenSearch request failed: {type(exc).__name__}"
            ) from exc
        if response.is_error:
            detail = response.text[:500].replace("\n", " ")
            raise OpenSearchRequestError(
                f"OpenSearch returned HTTP {response.status_code}: {detail}", response.status_code
            )
        if not response.content:
            return None
        return response.json()


class OpenSearchIndexManager(_OpenSearchHTTP):
    def __init__(
        self,
        base_url: str,
        index_name: str = "rag_chunks_v1",
        read_alias: str = "rag_chunks_read",
        write_alias: str = "rag_chunks_write",
        dimension: int = 1024,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool = True,
        timeout_seconds: float = 3.0,
    ) -> None:
        super().__init__(base_url, username, password, verify_ssl, timeout_seconds)
        self.index_name = index_name
        self.read_alias = read_alias
        self.write_alias = write_alias
        self.dimension = dimension

    async def ensure(self) -> dict[str, Any]:
        if self.index_name in {self.read_alias, self.write_alias}:
            raise ValueError("physical index and aliases must have distinct names")
        try:
            mapping = await self.request("GET", f"/{self.index_name}/_mapping")
        except OpenSearchRequestError as exc:
            if exc.status_code != 404:
                raise
            await self.request("PUT", f"/{self.index_name}", json=index_definition(self.dimension))
            mapping = await self.request("GET", f"/{self.index_name}/_mapping")
        self._validate_mapping(mapping)
        await self.verify_ik()
        await self._point_aliases((self.read_alias, self.write_alias))
        return {
            "index": self.index_name,
            "read_alias": self.read_alias,
            "write_alias": self.write_alias,
            "dimension": self.dimension,
            "ik": "verified",
        }

    def _validate_mapping(self, mapping: dict[str, Any]) -> None:
        root = mapping.get(self.index_name, {}).get("mappings", {})
        properties = root.get("properties", {})
        expected = {
            "content": "text",
            "title": "text",
            "content_vector": "knn_vector",
            "owner_id": "keyword",
            "knowledge_base_id": "keyword",
        }
        for field, field_type in expected.items():
            if properties.get(field, {}).get("type") != field_type:
                raise OpenSearchRequestError(f"mapping mismatch for field {field}")
        if properties["content_vector"].get("dimension") != self.dimension:
            raise OpenSearchRequestError("mapping mismatch for content_vector dimension")
        if properties["content"].get("analyzer") != "rag_index_analyzer":
            raise OpenSearchRequestError("mapping mismatch for content analyzer")
        if properties["content"].get("search_analyzer") != "rag_search_analyzer":
            raise OpenSearchRequestError("mapping mismatch for content search analyzer")

    async def verify_ik(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for analyzer in ("rag_index_analyzer", "rag_search_analyzer"):
            payload = await self.request(
                "POST",
                f"/{self.index_name}/_analyze",
                json={"analyzer": analyzer, "text": "中华人民共和国"},
            )
            tokens = [item["token"] for item in payload.get("tokens", [])]
            if not tokens:
                raise OpenSearchRequestError(f"analyzer {analyzer} returned no tokens")
            result[analyzer] = tokens
        return result

    async def _point_aliases(self, aliases: tuple[str, ...]) -> None:
        """Point all configured aliases in one atomic _aliases request."""
        actions: list[dict[str, Any]] = []
        for alias in aliases:
            existing: list[str] = []
            try:
                payload = await self.request("GET", f"/_alias/{alias}")
                existing = list(payload.keys())
            except OpenSearchRequestError as exc:
                if exc.status_code != 404:
                    raise
                # OpenSearch returns 404 for an alias lookup when a physical
                # index with the same name exists. Detect that conflict before
                # attempting _aliases, so the operator gets an actionable error.
                try:
                    await self.request("GET", f"/{alias}")
                except OpenSearchRequestError as index_exc:
                    if index_exc.status_code != 404:
                        raise
                else:
                    raise OpenSearchRequestError(
                        f"cannot create alias {alias!r}: a physical index with the same "
                        "name already exists; migrate or remove that index first"
                    )
            actions.extend(
                build_alias_actions(
                    index_name=self.index_name,
                    alias=alias,
                    existing_indices=existing,
                )
            )
        if actions:
            await self.request("POST", "/_aliases", json={"actions": actions})


def build_access_filters(
    *, user_id: str, role: str, knowledge_base_id: str, knowledge_base_version: str | None = None
) -> list[dict[str, Any]]:
    if not knowledge_base_id:
        raise ValueError("knowledge_base_id is required for retrieval")
    filters: list[dict[str, Any]] = [{"term": {"knowledge_base_id": knowledge_base_id}}]
    if role != "admin":
        filters.insert(0, {"term": {"owner_id": user_id}})
    if knowledge_base_version is not None:
        filters.append({"term": {"knowledge_base_version": knowledge_base_version}})
    return filters


def build_sparse_query(
    query: str, *, sparse_k: int, filters: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "size": sparse_k,
        "_source": {"excludes": ["content_vector"]},
        "query": {"bool": {"must": [{"match": {"content": query}}], "filter": filters}},
    }


def build_dense_query(
    vector: list[float], *, dense_k: int, filters: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "size": dense_k,
        "_source": {"excludes": ["content_vector"]},
        "query": {
            "knn": {
                "content_vector": {
                    "vector": vector,
                    "k": dense_k,
                    "filter": {"bool": {"filter": filters}},
                }
            }
        },
    }


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    score: float
    source: dict[str, Any]
    rank: int


def _hits(payload: dict[str, Any]) -> list[SearchHit]:
    return [
        SearchHit(
            chunk_id=str(hit.get("_source", {}).get("chunk_id", hit.get("_id", ""))),
            score=float(hit.get("_score") or 0.0),
            source=hit.get("_source", {}),
            rank=rank,
        )
        for rank, hit in enumerate(payload.get("hits", {}).get("hits", []), start=1)
    ]


class SparseRetriever(_OpenSearchHTTP):
    def __init__(self, *args: Any, read_alias: str = "rag_chunks_read", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read_alias = read_alias

    async def search(
        self, query: str, *, sparse_k: int, filters: list[dict[str, Any]]
    ) -> list[SearchHit]:
        payload = await self.request(
            "POST",
            f"/{self.read_alias}/_search",
            json=build_sparse_query(query, sparse_k=sparse_k, filters=filters),
        )
        return _hits(payload)


class DenseRetriever(_OpenSearchHTTP):
    def __init__(self, *args: Any, read_alias: str = "rag_chunks_read", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read_alias = read_alias

    async def search(
        self, vector: list[float], *, dense_k: int, filters: list[dict[str, Any]]
    ) -> list[SearchHit]:
        payload = await self.request(
            "POST",
            f"/{self.read_alias}/_search",
            json=build_dense_query(vector, dense_k=dense_k, filters=filters),
        )
        return _hits(payload)


@dataclass(frozen=True)
class ParallelRetrieval:
    dense: list[SearchHit]
    sparse: list[SearchHit]


async def retrieve_parallel(
    sparse: SparseRetriever,
    dense: DenseRetriever,
    query: str,
    vector: list[float],
    *,
    sparse_k: int,
    dense_k: int,
    filters: list[dict[str, Any]],
) -> ParallelRetrieval:
    sparse_hits, dense_hits = await asyncio.gather(
        sparse.search(query, sparse_k=sparse_k, filters=filters),
        dense.search(vector, dense_k=dense_k, filters=filters),
    )
    return ParallelRetrieval(dense=dense_hits, sparse=sparse_hits)
