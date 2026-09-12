from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.retrieval.circuit import CircuitOpenError, CircuitRegistry
from app.retrieval.deadline import Deadline
from app.retrieval.fusion import FusedHit, rrf_fuse
from app.retrieval.opensearch import DenseRetriever, SearchHit, SparseRetriever


class AllRetrieversFailed(RuntimeError):
    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class BranchResult:
    name: str
    hits: list[SearchHit]
    status: str
    latency_ms: int
    started_at: str
    ended_at: str
    circuit_state: str | None = None
    error_code: str | None = None


@dataclass
class RetrievalOutcome:
    dense: list[SearchHit]
    sparse: list[SearchHit]
    fused: list[FusedHit]
    degraded_reasons: list[str]
    stages: list[dict[str, Any]]
    timeout_summary: dict[str, Any]
    cache_hit: bool = False

    @property
    def degraded(self) -> bool:
        return bool(self.degraded_reasons)

    @property
    def status(self) -> str:
        return "degraded" if self.degraded else "completed"


async def _branch_call(
    name: str,
    retriever: SparseRetriever | DenseRetriever,
    argument: str | list[float],
    k_value: int,
    filters: list[dict[str, Any]],
    registry: CircuitRegistry,
) -> BranchResult:
    started_clock = time.monotonic()
    started_at = iso_now()
    breaker = await registry.get(f"opensearch.{name}")
    try:
        snapshot = await breaker.before_call()
    except CircuitOpenError:
        return BranchResult(name, [], "skipped", 0, started_at, iso_now(), "OPEN", "CIRCUIT_OPEN")
    try:
        if name == "sparse":
            hits = await retriever.search(argument, sparse_k=k_value, filters=filters)  # type: ignore[arg-type]
        else:
            hits = await retriever.search(argument, dense_k=k_value, filters=filters)  # type: ignore[arg-type]
    except asyncio.CancelledError:
        raise
    except Exception:
        snapshot = await breaker.record_failure()
        return BranchResult(
            name,
            [],
            "failed",
            int((time.monotonic() - started_clock) * 1000),
            started_at,
            iso_now(),
            snapshot.state,
            "RETRIEVAL_UNAVAILABLE",
        )
    await breaker.record_success()
    return BranchResult(
        name,
        hits,
        "completed",
        int((time.monotonic() - started_clock) * 1000),
        started_at,
        iso_now(),
        snapshot.state,
    )


def _stage(result: BranchResult, deadline: Deadline, k_value: int) -> dict[str, Any]:
    return {
        "name": f"{result.name}_retrieval",
        "status": result.status,
        "latency_ms": result.latency_ms,
        "started_at": result.started_at,
        "ended_at": result.ended_at,
        "timeout_ms": deadline.total_ms,
        "deadline_remaining_ms": deadline.remaining_ms,
        "retry_count": 0,
        "circuit_state": result.circuit_state,
        "fallback": "other_channel" if result.status != "completed" else None,
        "error_code": result.error_code,
        "metadata": {"k": k_value},
    }


async def retrieve_resilient(
    sparse: SparseRetriever,
    dense: DenseRetriever | None,
    query: str,
    vector: list[float] | None,
    *,
    sparse_k: int,
    dense_k: int,
    final_top_k: int,
    filters: list[dict[str, Any]],
    deadline: Deadline,
    registry: CircuitRegistry,
    rrf_k: int = 60,
) -> RetrievalOutcome:
    tasks: dict[str, asyncio.Task[BranchResult]] = {
        "sparse": asyncio.create_task(
            _branch_call("sparse", sparse, query, sparse_k, filters, registry)
        )
    }
    reasons: list[str] = []
    stages: list[dict[str, Any]] = []
    if vector is not None and dense is not None:
        tasks["dense"] = asyncio.create_task(
            _branch_call("dense", dense, vector, dense_k, filters, registry)
        )
    else:
        reasons.append("EMBEDDING_TIMEOUT")
        now = iso_now()
        stages.append(
            {
                "name": "dense_retrieval",
                "status": "skipped",
                "latency_ms": 0,
                "started_at": now,
                "ended_at": now,
                "timeout_ms": deadline.total_ms,
                "deadline_remaining_ms": deadline.remaining_ms,
                "retry_count": 0,
                "circuit_state": None,
                "fallback": "sparse",
                "error_code": "EMBEDDING_UNAVAILABLE",
                "metadata": {},
            }
        )

    done, pending = await asyncio.wait(tasks.values(), timeout=deadline.remaining_seconds)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    results: dict[str, BranchResult] = {}
    for name, task in tasks.items():
        if task in pending:
            results[name] = BranchResult(
                name,
                [],
                "timeout",
                deadline.total_ms,
                iso_now(),
                iso_now(),
                None,
                "RETRIEVAL_TIMEOUT",
            )
        else:
            results[name] = task.result()

    for name, result in results.items():
        if result.status == "failed" or result.status == "skipped":
            reasons.append(
                "DENSE_RETRIEVAL_UNAVAILABLE"
                if name == "dense"
                else "SPARSE_RETRIEVAL_UNAVAILABLE"
            )
        elif result.status == "timeout":
            reasons.append("RETRIEVAL_TIMEOUT")
        stages.append(_stage(result, deadline, dense_k if name == "dense" else sparse_k))

    dense_result = results.get("dense")
    sparse_result = results["sparse"]
    if not any(
        result and result.status == "completed"
        for result in (dense_result, sparse_result)
    ):
        raise AllRetrieversFailed(
            "all retrieval branches failed",
            timed_out=bool(results) and all(
                result.status in {"timeout", "skipped"}
                for result in results.values()
            ) and any(result.status == "timeout" for result in results.values()),
        )
    fused = rrf_fuse(
        dense_result.hits if dense_result else [],
        sparse_result.hits,
        final_top_k=final_top_k,
        rrf_k=rrf_k,
    )
    timeout_stages = [item["name"] for item in stages if item["status"] == "timeout"]
    return RetrievalOutcome(
        dense=dense_result.hits if dense_result else [],
        sparse=sparse_result.hits,
        fused=fused,
        degraded_reasons=list(dict.fromkeys(reasons)),
        stages=stages,
        timeout_summary={
            "timed_out": bool(timeout_stages),
            "timeout_stages": timeout_stages,
            "deadline_exceeded": deadline.remaining_seconds <= 0,
            "circuit_opened_dependencies": [
                f"opensearch.{name}"
                for name, result in results.items()
                if result.circuit_state == "OPEN"
            ],
        },
    )
