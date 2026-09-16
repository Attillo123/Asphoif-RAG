from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.evaluators import AbstentionEvaluator, CitationEvaluator, RagasEvaluator, RetrievalMetricsEvaluator
from app.cache.redis import normalize_query
from app.models.evaluation import EvaluationCaseResult, EvaluationDatasetCase, EvaluationDatasetSnapshot, EvaluationRun
from app.models.trace import QueryTrace


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_safe(value: Any) -> Any:
    """Convert NumPy/RAGAS non-finite values to JSON null for MySQL JSON."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


async def execute_run(session: AsyncSession, run: EvaluationRun, cases: list[EvaluationDatasetCase], settings: Any | None = None) -> EvaluationRun:
    run.status = "running"
    run.started_at = _now()
    manifest = dict(run.run_manifest or {})
    bindings = manifest.get("trace_bindings", {})
    snapshot = await session.scalar(select(EvaluationDatasetSnapshot).where(EvaluationDatasetSnapshot.id == run.snapshot_id))
    evaluators = [RetrievalMetricsEvaluator(), CitationEvaluator(), AbstentionEvaluator(), RagasEvaluator(manifest, settings)]
    aggregate: dict[str, list[float]] = {}
    try:
        for case in cases:
            request_id = bindings.get(case.id) or (case.annotations or {}).get("request_id")
            if not request_id:
                canonical_hash = hashlib.sha256(normalize_query(case.question).encode("utf-8")).hexdigest()
                legacy_hash = hashlib.sha256(case.question.strip().encode("utf-8")).hexdigest()
                trace_statement = (
                    select(QueryTrace)
                    .where(QueryTrace.query_hash.in_((canonical_hash, legacy_hash)), QueryTrace.status.in_(("completed", "degraded")))
                    .order_by(QueryTrace.created_at.desc())
                )
                if run.created_by:
                    trace_statement = trace_statement.where(QueryTrace.user_id == run.created_by)
                if snapshot and snapshot.knowledge_base_id:
                    trace_statement = trace_statement.where(QueryTrace.knowledge_base_id == snapshot.knowledge_base_id)
                fallback_trace = await session.scalar(trace_statement)
                if fallback_trace is not None:
                    request_id = fallback_trace.request_id
                    bindings[case.id] = request_id
            trace = await session.scalar(select(QueryTrace).where(QueryTrace.request_id == request_id)) if request_id else None
            result = await session.scalar(select(EvaluationCaseResult).where(EvaluationCaseResult.run_id == run.id, EvaluationCaseResult.case_id == case.id))
            if result is None:
                result = EvaluationCaseResult(run_id=run.id, case_id=case.id, status="failed", metrics={})
                session.add(result)
            if trace is None:
                result.status = "skipped"
                result.error_message = "TRACE_NOT_BOUND"
                result.metrics = {"status": "skipped", "reason": "TRACE_NOT_BOUND"}
                continue
            trace.evaluation_run_id = run.id
            trace.evaluation_case_id = case.id
            metrics: dict[str, Any] = {}
            diagnosis: dict[str, Any] = {}
            for evaluator in evaluators:
                values, detail = evaluator.evaluate(case, trace)
                values = _json_safe(values)
                detail = _json_safe(detail)
                metrics[evaluator.name] = values
                diagnosis[evaluator.name] = detail
                for key, value in values.items():
                    if isinstance(value, (int, float)):
                        aggregate.setdefault(key, []).append(float(value))
                # RAGAS returns its numeric metrics under `scores`; flatten
                # them into run-level aggregates for dashboards and regression
                # comparisons while keeping the per-case nested payload intact.
                scores = values.get("scores") if isinstance(values, dict) else None
                if isinstance(scores, dict):
                    for score_name, score_value in scores.items():
                        if isinstance(score_value, (int, float)) and math.isfinite(float(score_value)):
                            aggregate.setdefault(f"ragas_{score_name}", []).append(float(score_value))
            snapshot = trace.retrieval_snapshot or {}
            result.request_id = trace.request_id
            result.status = "completed"
            result.retrieved_chunk_ids = [str(item.get("chunk_id")) for item in snapshot.get("candidates", []) if item.get("chunk_id")]
            result.context_snapshot = snapshot
            result.answer = (trace.output_snapshot or {}).get("answer")
            result.citations = (trace.output_snapshot or {}).get("citations")
            result.metrics = _json_safe(metrics)
            result.diagnosis = _json_safe(diagnosis)
            result.latency_ms = trace.total_latency_ms
            result.input_tokens = trace.input_tokens
            result.output_tokens = trace.output_tokens
            result.cost = trace.cost
        run.aggregate_metrics = {key: round(sum(values) / len(values), 6) for key, values in aggregate.items() if values}
        run.run_manifest = {**manifest, "trace_bindings": bindings}
        run.status = "completed"
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:1000]
    run.finished_at = _now()
    await session.commit()
    await session.refresh(run)
    return run
