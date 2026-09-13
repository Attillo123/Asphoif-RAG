from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.evaluators import AbstentionEvaluator, CitationEvaluator, RagasEvaluator, RetrievalMetricsEvaluator
from app.models.evaluation import EvaluationCaseResult, EvaluationDatasetCase, EvaluationRun
from app.models.trace import QueryTrace


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def execute_run(session: AsyncSession, run: EvaluationRun, cases: list[EvaluationDatasetCase], settings: Any | None = None) -> EvaluationRun:
    run.status = "running"
    run.started_at = _now()
    manifest = dict(run.run_manifest or {})
    bindings = manifest.get("trace_bindings", {})
    evaluators = [RetrievalMetricsEvaluator(), CitationEvaluator(), AbstentionEvaluator(), RagasEvaluator(manifest, settings)]
    aggregate: dict[str, list[float]] = {}
    try:
        for case in cases:
            request_id = bindings.get(case.id) or (case.annotations or {}).get("request_id")
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
                metrics[evaluator.name] = values
                diagnosis[evaluator.name] = detail
                for key, value in values.items():
                    if isinstance(value, (int, float)):
                        aggregate.setdefault(key, []).append(float(value))
            snapshot = trace.retrieval_snapshot or {}
            result.request_id = trace.request_id
            result.status = "completed"
            result.retrieved_chunk_ids = [str(item.get("chunk_id")) for item in snapshot.get("candidates", []) if item.get("chunk_id")]
            result.context_snapshot = snapshot
            result.answer = (trace.output_snapshot or {}).get("answer")
            result.citations = (trace.output_snapshot or {}).get("citations")
            result.metrics = metrics
            result.diagnosis = diagnosis
            result.latency_ms = trace.total_latency_ms
            result.input_tokens = trace.input_tokens
            result.output_tokens = trace.output_tokens
            result.cost = trace.cost
        run.aggregate_metrics = {key: round(sum(values) / len(values), 6) for key, values in aggregate.items() if values}
        run.status = "completed"
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:1000]
    run.finished_at = _now()
    await session.commit()
    await session.refresh(run)
    return run
