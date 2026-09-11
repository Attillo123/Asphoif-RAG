from app.retrieval.opensearch import (
    build_access_filters,
    build_alias_actions,
    build_dense_query,
    build_sparse_query,
    index_definition,
)


def test_alias_actions_are_idempotent_when_alias_already_points_to_target() -> None:
    assert build_alias_actions(
        index_name="rag_chunks_v1",
        alias="rag_chunks_read",
        existing_indices=["rag_chunks_v1"],
    ) == []


def test_alias_actions_replace_old_target_and_add_new_target() -> None:
    assert build_alias_actions(
        index_name="rag_chunks_v2",
        alias="rag_chunks_read",
        existing_indices=["rag_chunks_v1"],
    ) == [
        {"remove": {"index": "rag_chunks_v1", "alias": "rag_chunks_read"}},
        {"add": {"index": "rag_chunks_v2", "alias": "rag_chunks_read"}},
    ]


def test_index_definition_matches_phase4_contract() -> None:
    definition = index_definition()
    properties = definition["mappings"]["properties"]

    assert definition["settings"]["index.knn"] is True
    assert properties["content"]["type"] == "text"
    assert properties["title"]["type"] == "text"
    assert properties["content_vector"] == {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
    }
    assert properties["owner_id"]["type"] == "keyword"
    assert properties["knowledge_base_id"]["type"] == "keyword"


def test_personal_access_filter_is_injected() -> None:
    filters = build_access_filters(
        user_id="user-a",
        role="personal",
        knowledge_base_id="kb-a",
        knowledge_base_version="1",
    )

    assert {"term": {"owner_id": "user-a"}} in filters
    assert {"term": {"knowledge_base_id": "kb-a"}} in filters
    assert {"term": {"knowledge_base_version": "1"}} in filters


def test_admin_still_requires_knowledge_base_and_does_not_use_owner_filter() -> None:
    filters = build_access_filters(
        user_id="admin",
        role="admin",
        knowledge_base_id="kb-a",
    )

    assert filters == [{"term": {"knowledge_base_id": "kb-a"}}]


def test_sparse_and_dense_queries_use_independent_k_values() -> None:
    filters = [{"term": {"knowledge_base_id": "kb-a"}}]
    sparse = build_sparse_query("人工智能就业", sparse_k=7, filters=filters)
    dense = build_dense_query([0.1, 0.2], dense_k=11, filters=filters)

    assert sparse["size"] == 7
    assert sparse["query"]["bool"]["must"] == [{"match": {"content": "人工智能就业"}}]
    assert dense["size"] == 11
    assert dense["query"]["knn"]["content_vector"]["k"] == 11
    assert dense["query"]["knn"]["content_vector"]["filter"]["bool"]["filter"] == filters
