from __future__ import annotations

import math
from collections.abc import Sequence


def retrieval_metrics(retrieved: Sequence[str], gold: Sequence[str], k: int = 5) -> dict[str, float | int]:
    """Compute ranking metrics from stable chunk ids, without an LLM."""
    ranked = list(dict.fromkeys(str(x) for x in retrieved))[: max(0, k)]
    relevant = {str(x) for x in gold}
    hits = [item for item in ranked if item in relevant]
    hit = int(bool(hits))
    recall = len(set(hits)) / len(relevant) if relevant else 0.0
    precision = len(hits) / len(ranked) if ranked else 0.0
    reciprocal_rank = next((1.0 / (index + 1) for index, item in enumerate(ranked) if item in relevant), 0.0)
    dcg = sum((1.0 / math.log2(index + 2)) for index, item in enumerate(ranked) if item in relevant)
    ideal_hits = min(len(relevant), len(ranked))
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return {
        f"hit@{k}": hit,
        f"recall@{k}": round(recall, 6),
        f"precision@{k}": round(precision, 6),
        f"mrr@{k}": round(reciprocal_rank, 6),
        f"ndcg@{k}": round(dcg / idcg, 6) if idcg else 0.0,
        "retrieved_count": len(ranked),
        "gold_count": len(relevant),
    }

