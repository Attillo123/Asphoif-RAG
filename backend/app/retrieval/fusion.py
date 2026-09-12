from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.retrieval.opensearch import SearchHit


@dataclass
class FusedHit:
    chunk_id: str
    score: float
    source: dict[str, Any]
    rank: int
    channels: list[str] = field(default_factory=list)
    dense_score: float | None = None
    sparse_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None


def rrf_fuse(
    dense: list[SearchHit],
    sparse: list[SearchHit],
    *,
    final_top_k: int,
    rrf_k: int = 60,
) -> list[FusedHit]:
    """Fuse independent result lists without relying on OpenSearch Hybrid DSL."""
    by_id: dict[str, FusedHit] = {}
    for channel, hits in (("dense", dense), ("sparse", sparse)):
        for hit in hits:
            current = by_id.get(hit.chunk_id)
            if current is None:
                current = FusedHit(
                    chunk_id=hit.chunk_id,
                    score=0.0,
                    source=hit.source,
                    rank=0,
                )
                by_id[hit.chunk_id] = current
            current.score += 1.0 / (rrf_k + hit.rank)
            if channel not in current.channels:
                current.channels.append(channel)
            if channel == "dense":
                current.dense_score = hit.score
                current.dense_rank = hit.rank
            else:
                current.sparse_score = hit.score
                current.sparse_rank = hit.rank

    ordered = sorted(by_id.values(), key=lambda hit: (-hit.score, hit.chunk_id))
    for rank, hit in enumerate(ordered[:final_top_k], start=1):
        hit.rank = rank
    return ordered[:final_top_k]
