from app.evaluation.metrics import retrieval_metrics


def test_retrieval_metrics_uses_ranked_chunk_ids() -> None:
    metrics = retrieval_metrics(["miss", "gold-b", "gold-a"], ["gold-a", "gold-b"], k=3)

    assert metrics["hit@3"] == 1
    assert metrics["recall@3"] == 1.0
    assert metrics["precision@3"] == 2 / 3
    assert metrics["mrr@3"] == 0.5
    assert metrics["ndcg@3"] > 0


def test_retrieval_metrics_handles_empty_gold_without_dividing_by_zero() -> None:
    metrics = retrieval_metrics(["chunk-a"], [], k=5)

    assert metrics["hit@5"] == 0
    assert metrics["recall@5"] == 0.0
