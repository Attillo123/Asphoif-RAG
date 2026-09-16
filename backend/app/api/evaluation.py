from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode, api_response
from app.core.config import Settings, get_settings
from app.cache.redis import normalize_query
from app.core.security import get_current_user
from app.db.ids import new_id
from app.db.session import get_db_session
from app.evaluation.service import execute_run
from app.models.evaluation import EvaluationCaseResult, EvaluationDatasetCase, EvaluationDatasetSnapshot, EvaluationRun
from app.models.trace import QueryTrace
from app.models.user import User
from app.schemas.evaluation import DatasetSnapshotCreate, HumanReviewInput, RunCreate

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


def _visible_snapshot(user: User, snapshot_id: str):
    statement = select(EvaluationDatasetSnapshot).where(EvaluationDatasetSnapshot.id == snapshot_id)
    if user.role != "admin":
        statement = statement.where(EvaluationDatasetSnapshot.created_by == user.id)
    return statement


def _snapshot_payload(snapshot: EvaluationDatasetSnapshot) -> dict[str, Any]:
    return {"id": snapshot.id, "dataset_id": snapshot.dataset_id, "snapshot_version": snapshot.snapshot_version, "name": snapshot.name, "status": snapshot.status, "knowledge_base_id": snapshot.knowledge_base_id, "knowledge_base_version": snapshot.knowledge_base_version, "source_type": snapshot.source_type, "dataset_sha256": snapshot.dataset_sha256, "case_count": snapshot.case_count, "schema_version": snapshot.schema_version, "created_at": snapshot.created_at, "updated_at": snapshot.updated_at}

def _case_payload(case: EvaluationDatasetCase) -> dict[str, Any]:
    return {"id": case.id, "snapshot_id": case.snapshot_id, "ordinal": case.ordinal, "question": case.question, "reference_answer": case.reference_answer, "gold_chunk_ids": case.gold_chunk_ids, "question_type": case.question_type, "is_unanswerable": case.is_unanswerable, "requires_citation": case.requires_citation, "allowed_answers": case.allowed_answers, "annotations": case.annotations, "case_sha256": case.case_sha256}

def _result_payload(row: EvaluationCaseResult) -> dict[str, Any]:
    return {"id": row.id, "run_id": row.run_id, "case_id": row.case_id, "request_id": row.request_id, "status": row.status, "retrieved_chunk_ids": row.retrieved_chunk_ids, "context_snapshot": row.context_snapshot, "answer": row.answer, "citations": row.citations, "metrics": row.metrics, "diagnosis": row.diagnosis, "latency_ms": row.latency_ms, "error_message": row.error_message}


@router.post("/snapshots")
async def create_snapshot(payload: DatasetSnapshotCreate, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    canonical = [case.model_dump(mode="json") for case in payload.cases]
    digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    snapshot = EvaluationDatasetSnapshot(dataset_id=payload.dataset_id or new_id(), snapshot_version=payload.snapshot_version, name=payload.name, source_type=payload.source_type, knowledge_base_id=payload.knowledge_base_id, knowledge_base_version=payload.knowledge_base_version, dataset_sha256=digest, case_count=len(canonical), schema_version="v1", metadata_json=payload.metadata, created_by=user.id)
    session.add(snapshot)
    for ordinal, case in enumerate(payload.cases, 1):
        case_data = case.model_dump()
        case_digest = hashlib.sha256(json.dumps(case_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        session.add(EvaluationDatasetCase(snapshot=snapshot, ordinal=ordinal, case_sha256=case_digest, **case_data))
    await session.commit()
    await session.refresh(snapshot)
    return api_response(success=True, code=0, message="ok", data=_snapshot_payload(snapshot))


@router.get("/snapshots")
async def list_snapshots(session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    statement = select(EvaluationDatasetSnapshot).order_by(desc(EvaluationDatasetSnapshot.created_at))
    if user.role != "admin": statement = statement.where(EvaluationDatasetSnapshot.created_by == user.id)
    rows = (await session.scalars(statement)).all()
    return api_response(success=True, code=0, message="ok", data={"items": [_snapshot_payload(row) for row in rows]})


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    snapshot = await session.scalar(_visible_snapshot(user, snapshot_id))
    if snapshot is None: raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评估数据集不存在", "RESOURCE_NOT_FOUND", 404)
    cases = (await session.scalars(select(EvaluationDatasetCase).where(EvaluationDatasetCase.snapshot_id == snapshot.id).order_by(EvaluationDatasetCase.ordinal))).all()
    return api_response(success=True, code=0, message="ok", data={"snapshot": _snapshot_payload(snapshot), "cases": [_case_payload(case) for case in cases]})


@router.post("/snapshots/{snapshot_id}/publish")
async def publish_snapshot(snapshot_id: str, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    snapshot = await session.scalar(_visible_snapshot(user, snapshot_id))
    if snapshot is None: raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评估数据集不存在", "RESOURCE_NOT_FOUND", 404)
    snapshot.status = "published"
    await session.commit()
    # Async sessions expire ORM attributes on commit by default. Refresh the
    # row before building the response to avoid an implicit lazy load outside
    # greenlet_spawn (MissingGreenlet).
    await session.refresh(snapshot)
    return api_response(success=True, code=0, message="ok", data=_snapshot_payload(snapshot))


@router.post("/runs")
async def create_run(payload: RunCreate, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user), settings: Settings = Depends(get_settings)):
    snapshot = await session.scalar(_visible_snapshot(user, payload.snapshot_id))
    if snapshot is None: raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评估数据集不存在", "RESOURCE_NOT_FOUND", 404)
    if snapshot.status != "published": raise AppError(ErrorCode.INVALID_REQUEST, "数据集快照必须先发布", "SNAPSHOT_NOT_PUBLISHED", 409)
    cases = (await session.scalars(select(EvaluationDatasetCase).where(EvaluationDatasetCase.snapshot_id == snapshot.id))).all()
    # The console can bind a Case to the most recent online Trace for the same
    # question. Explicit bindings always win; the resolved map is persisted in
    # run_manifest so the run remains reproducible after creation.
    trace_bindings = dict(payload.trace_bindings)
    for case in cases:
        if case.id in trace_bindings:
            continue
        canonical_hash = hashlib.sha256(normalize_query(case.question).encode("utf-8")).hexdigest()
        legacy_hash = hashlib.sha256(case.question.strip().encode("utf-8")).hexdigest()
        trace_statement = (
            select(QueryTrace)
            .where(QueryTrace.query_hash.in_((canonical_hash, legacy_hash)), QueryTrace.status.in_(("completed", "degraded")))
            .order_by(desc(QueryTrace.created_at))
        )
        if user.role != "admin":
            trace_statement = trace_statement.where(QueryTrace.user_id == user.id)
        if snapshot.knowledge_base_id:
            trace_statement = trace_statement.where(QueryTrace.knowledge_base_id == snapshot.knowledge_base_id)
        trace = await session.scalar(trace_statement)
        if trace is not None:
            trace_bindings[case.id] = trace.request_id
    case_ids = {case.id for case in cases}
    invalid_case_ids = set(trace_bindings) - case_ids
    if invalid_case_ids:
        raise AppError(ErrorCode.INVALID_REQUEST, "Trace 绑定包含不属于数据集的 Case", "INVALID_TRACE_BINDING", 422)
    for case_id, request_id in trace_bindings.items():
        trace_statement = select(QueryTrace).where(QueryTrace.request_id == request_id)
        if user.role != "admin":
            trace_statement = trace_statement.where(QueryTrace.user_id == user.id)
        trace = await session.scalar(trace_statement)
        if trace is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "绑定的 Trace 不存在或无权限访问", "TRACE_NOT_FOUND", 404)
        if snapshot.knowledge_base_id and trace.knowledge_base_id != snapshot.knowledge_base_id:
            raise AppError(ErrorCode.INVALID_REQUEST, "Trace 与数据集知识库不一致", "TRACE_KNOWLEDGE_BASE_MISMATCH", 422)
    defaults = {"judge_base_url": settings.evaluation_judge_base_url, "judge_model": settings.evaluation_judge_model, "judge_prompt_version": settings.evaluation_judge_prompt_version, "judge_api_key_configured": bool(settings.evaluation_judge_api_key), "evaluator": "ragas"}
    supplied_manifest = {key: value for key, value in payload.manifest.items() if key != "judge_api_key"}
    manifest = {**{k: v for k, v in defaults.items() if v is not None}, **supplied_manifest, "trace_bindings": trace_bindings, "cache_policy": "disabled", "snapshot_id": snapshot.id}
    run = EvaluationRun(snapshot_id=snapshot.id, run_manifest=manifest, created_by=user.id, status="pending")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return api_response(success=True, code=0, message="ok", data={"id": run.id, "status": run.status, "run_manifest": run.run_manifest})


@router.post("/runs/{run_id}/execute")
async def run_evaluation(run_id: str, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user), settings: Settings = Depends(get_settings)):
    statement = select(EvaluationRun).join(EvaluationDatasetSnapshot).where(EvaluationRun.id == run_id)
    if user.role != "admin": statement = statement.where(EvaluationRun.created_by == user.id)
    run = await session.scalar(statement)
    if run is None: raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评估运行不存在", "RESOURCE_NOT_FOUND", 404)
    cases = (await session.scalars(select(EvaluationDatasetCase).where(EvaluationDatasetCase.snapshot_id == run.snapshot_id).order_by(EvaluationDatasetCase.ordinal))).all()
    await execute_run(session, run, cases, settings)
    return api_response(success=True, code=0, message="ok", data={"id": run.id, "status": run.status, "aggregate_metrics": run.aggregate_metrics, "error_message": run.error_message})


@router.get("/runs")
async def list_runs(session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    statement = select(EvaluationRun).order_by(desc(EvaluationRun.created_at))
    if user.role != "admin": statement = statement.where(EvaluationRun.created_by == user.id)
    rows = (await session.scalars(statement)).all()
    data = [{"id": row.id, "snapshot_id": row.snapshot_id, "status": row.status, "run_manifest": row.run_manifest, "aggregate_metrics": row.aggregate_metrics, "created_at": row.created_at, "finished_at": row.finished_at} for row in rows]
    return api_response(success=True, code=0, message="ok", data={"items": data})


@router.get("/runs/{run_id}/results")
async def run_results(run_id: str, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    run = await session.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id, *( [] if user.role == "admin" else [EvaluationRun.created_by == user.id] )))
    if run is None: raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评估运行不存在", "RESOURCE_NOT_FOUND", 404)
    rows = (await session.scalars(select(EvaluationCaseResult).where(EvaluationCaseResult.run_id == run_id).order_by(EvaluationCaseResult.created_at))).all()
    return api_response(success=True, code=0, message="ok", data={"items": [_result_payload(row) for row in rows], "aggregate_metrics": run.aggregate_metrics})


@router.post("/runs/{run_id}/results/{result_id}/review")
async def review_result(run_id: str, result_id: str, payload: HumanReviewInput, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    run = await session.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id, *( [] if user.role == "admin" else [EvaluationRun.created_by == user.id] )))
    result = await session.scalar(select(EvaluationCaseResult).where(EvaluationCaseResult.id == result_id, EvaluationCaseResult.run_id == run_id)) if run else None
    if result is None: raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评估结果不存在", "RESOURCE_NOT_FOUND", 404)
    metrics = dict(result.metrics or {})
    metrics["human_review"] = {"scores": payload.scores, "reviewer_id": user.id, "reviewed_at": datetime.now(timezone.utc).isoformat(), "rubric_version": payload.rubric_version, "note": payload.reviewer_note}
    result.metrics = metrics
    await session.commit()
    return api_response(success=True, code=0, message="ok", data={"id": result.id, "human_review": metrics["human_review"]})


@router.get("/traces")
async def list_traces(session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user), knowledge_base_id: str | None = None, trace_type: str | None = None, limit: int = Query(default=50, ge=1, le=200)):
    statement = select(QueryTrace).order_by(desc(QueryTrace.created_at)).limit(limit)
    if user.role != "admin": statement = statement.where(QueryTrace.user_id == user.id)
    if knowledge_base_id: statement = statement.where(QueryTrace.knowledge_base_id == knowledge_base_id)
    if trace_type: statement = statement.where(QueryTrace.trace_type == trace_type)
    rows = (await session.scalars(statement)).all()
    return api_response(success=True, code=0, message="ok", data={"items": [{"request_id": row.request_id, "trace_type": row.trace_type, "status": row.status, "knowledge_base_id": row.knowledge_base_id, "knowledge_base_version": row.knowledge_base_version, "evaluation_run_id": row.evaluation_run_id, "evaluation_case_id": row.evaluation_case_id, "retrieval_snapshot": row.retrieval_snapshot, "output_snapshot": row.output_snapshot, "degraded_reasons": row.degraded_reasons, "total_latency_ms": row.total_latency_ms, "created_at": row.created_at} for row in rows]})


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str, session: AsyncSession = Depends(get_db_session), user: User = Depends(get_current_user)):
    run = await session.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id, *( [] if user.role == "admin" else [EvaluationRun.created_by == user.id] )))
    if run is None: raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评估运行不存在", "RESOURCE_NOT_FOUND", 404)
    rows = (await session.scalars(select(EvaluationCaseResult).where(EvaluationCaseResult.run_id == run_id))).all()
    lines = [json.dumps({"run_id": run.id, "aggregate_metrics": run.aggregate_metrics}, ensure_ascii=False)]
    lines.extend(json.dumps({"result_id": row.id, "case_id": row.case_id, "request_id": row.request_id, "status": row.status, "metrics": row.metrics, "diagnosis": row.diagnosis}, ensure_ascii=False) for row in rows)
    return PlainTextResponse("\n".join(lines), media_type="application/x-ndjson")
