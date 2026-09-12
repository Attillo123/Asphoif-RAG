from __future__ import annotations

import asyncio

import pytest

from app.cache.redis import cache_hash, normalize_query
from app.retrieval.circuit import CircuitOpenError, CircuitRegistry
from app.retrieval.deadline import Deadline
from app.retrieval.fusion import rrf_fuse
from app.retrieval.opensearch import SearchHit
from app.retrieval.service import AllRetrieversFailed, retrieve_resilient


def hit(chunk_id: str, rank: int, score: float = 1.0) -> SearchHit:
    return SearchHit(chunk_id, score, {"chunk_id": chunk_id}, rank)


def test_rrf_deduplicates_and_preserves_channel_metadata() -> None:
    result = rrf_fuse(
        [hit("same", 1), hit("dense-only", 2)],
        [hit("same", 1), hit("sparse-only", 2)],
        final_top_k=3,
    )

    assert [item.chunk_id for item in result] == ["same", "dense-only", "sparse-only"]
    assert result[0].channels == ["dense", "sparse"]
    assert result[0].dense_rank == 1
    assert result[0].sparse_rank == 1
    assert [item.rank for item in result] == [1, 2, 3]


def test_cache_normalization_and_hash_are_deterministic() -> None:
    assert normalize_query("  人工智能　就业  ") == "人工智能 就业"
    assert cache_hash({"b": 2, "a": 1}) == cache_hash({"a": 1, "b": 2})


class FakeRetriever:
    def __init__(self, result: list[SearchHit] | Exception, delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay
        self.started: float | None = None

    async def search(self, *_args, **_kwargs):
        self.started = asyncio.get_running_loop().time()
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_one_failed_branch_is_degraded_but_keeps_other_branch() -> None:
    sparse = FakeRetriever([hit("sparse", 1)])
    dense = FakeRetriever(RuntimeError("dense down"))
    registry = CircuitRegistry(
        failure_threshold=5,
        failure_window_seconds=30,
        open_seconds=15,
        half_open_max_calls=2,
    )

    result = await retrieve_resilient(
        sparse,
        dense,
        "问题",
        [0.1],
        sparse_k=5,
        dense_k=7,
        final_top_k=5,
        filters=[],
        deadline=Deadline(1),
        registry=registry,
    )

    assert [item.chunk_id for item in result.sparse] == ["sparse"]
    assert result.dense == []
    assert result.status == "degraded"
    assert "DENSE_RETRIEVAL_UNAVAILABLE" in result.degraded_reasons


@pytest.mark.asyncio
async def test_both_failed_branches_raise() -> None:
    sparse = FakeRetriever(RuntimeError("sparse down"))
    dense = FakeRetriever(RuntimeError("dense down"))
    registry = CircuitRegistry(
        failure_threshold=5,
        failure_window_seconds=30,
        open_seconds=15,
        half_open_max_calls=2,
    )

    with pytest.raises(AllRetrieversFailed):
        await retrieve_resilient(
            sparse,
            dense,
            "问题",
            [0.1],
            sparse_k=5,
            dense_k=5,
            final_top_k=5,
            filters=[],
            deadline=Deadline(1),
            registry=registry,
        )


@pytest.mark.asyncio
async def test_all_timed_out_branches_are_marked_timed_out() -> None:
    sparse = FakeRetriever([], delay=0.2)
    dense = FakeRetriever([], delay=0.2)
    registry = CircuitRegistry(
        failure_threshold=5,
        failure_window_seconds=30,
        open_seconds=15,
        half_open_max_calls=2,
    )

    with pytest.raises(AllRetrieversFailed) as error:
        await retrieve_resilient(
            sparse,
            dense,
            "问题",
            [0.1],
            sparse_k=5,
            dense_k=5,
            final_top_k=5,
            filters=[],
            deadline=Deadline(0.01),
            registry=registry,
        )

    assert error.value.timed_out is True


@pytest.mark.asyncio
async def test_empty_successful_branches_are_a_valid_no_evidence_result() -> None:
    sparse = FakeRetriever([])
    dense = FakeRetriever([])
    registry = CircuitRegistry(
        failure_threshold=5,
        failure_window_seconds=30,
        open_seconds=15,
        half_open_max_calls=2,
    )

    result = await retrieve_resilient(
        sparse,
        dense,
        "没有答案的问题",
        [0.1],
        sparse_k=5,
        dense_k=5,
        final_top_k=5,
        filters=[],
        deadline=Deadline(1),
        registry=registry,
    )

    assert result.status == "completed"
    assert result.fused == []


@pytest.mark.asyncio
async def test_dense_and_sparse_start_in_parallel_and_share_deadline() -> None:
    sparse = FakeRetriever([hit("sparse", 1)], delay=0.05)
    dense = FakeRetriever([hit("dense", 1)], delay=0.05)
    registry = CircuitRegistry(
        failure_threshold=5,
        failure_window_seconds=30,
        open_seconds=15,
        half_open_max_calls=2,
    )
    started = asyncio.get_running_loop().time()

    result = await retrieve_resilient(
        sparse,
        dense,
        "问题",
        [0.1],
        sparse_k=5,
        dense_k=11,
        final_top_k=5,
        filters=[],
        deadline=Deadline(1),
        registry=registry,
    )

    assert result.status == "completed"
    assert abs(sparse.started - dense.started) < 0.03
    assert asyncio.get_running_loop().time() - started < 0.15


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold_and_rejects_calls() -> None:
    registry = CircuitRegistry(
        failure_threshold=2,
        failure_window_seconds=30,
        open_seconds=15,
        half_open_max_calls=1,
    )
    breaker = await registry.get("opensearch.dense")
    await breaker.before_call()
    await breaker.record_failure()
    await breaker.before_call()
    snapshot = await breaker.record_failure()

    assert snapshot.state == "OPEN"
    with pytest.raises(CircuitOpenError):
        await breaker.before_call()
