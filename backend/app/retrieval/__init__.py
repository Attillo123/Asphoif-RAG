"""OpenSearch retrieval adapters and index management."""

from app.retrieval.opensearch import (
    DenseRetriever,
    OpenSearchIndexManager,
    OpenSearchRequestError,
    ParallelRetrieval,
    SearchHit,
    SparseRetriever,
    build_access_filters,
    build_dense_query,
    build_sparse_query,
    index_definition,
    retrieve_parallel,
)

__all__ = [
    "DenseRetriever",
    "OpenSearchIndexManager",
    "OpenSearchRequestError",
    "ParallelRetrieval",
    "SearchHit",
    "SparseRetriever",
    "build_access_filters",
    "build_dense_query",
    "build_sparse_query",
    "index_definition",
    "retrieve_parallel",
]
