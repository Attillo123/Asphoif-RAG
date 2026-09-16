from __future__ import annotations

import math
from typing import Any

import json

from app.evaluation.metrics import retrieval_metrics


class RetrievalMetricsEvaluator:
    name = "deterministic_retrieval"

    def evaluate(self, case: Any, trace: Any, k: int = 5) -> tuple[dict[str, Any], dict[str, Any]]:
        snapshot = trace.retrieval_snapshot or {}
        candidates = snapshot.get("candidates", [])
        retrieved = [str(item.get("chunk_id")) for item in candidates if item.get("chunk_id")]
        metrics = retrieval_metrics(retrieved, case.gold_chunk_ids or [], k)
        return metrics, {"retrieved_chunk_ids": retrieved, "gold_chunk_ids": list(case.gold_chunk_ids or [])}


class CitationEvaluator:
    name = "citation"

    def evaluate(self, case: Any, trace: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        output = trace.output_snapshot or {}
        citations = output.get("citations") or []
        cited = {str(item.get("chunk_id")) for item in citations if item.get("chunk_id")}
        gold = {str(item) for item in (case.gold_chunk_ids or [])}
        valid = len(cited & gold) / len(cited) if cited else 0.0
        completeness = len(cited & gold) / len(gold) if gold else (1.0 if not case.requires_citation else 0.0)
        return {
            "citation_correctness": round(valid, 6),
            "citation_completeness": round(completeness, 6),
            "citation_count": len(cited),
        }, {"cited_chunk_ids": sorted(cited), "unsupported_chunk_ids": sorted(cited - gold)}


class AbstentionEvaluator:
    name = "abstention"

    def evaluate(self, case: Any, trace: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        answer = str((trace.output_snapshot or {}).get("answer") or "").strip()
        has_evidence = bool((trace.retrieval_snapshot or {}).get("candidates"))
        refusal_markers = ("无法", "没有足够", "未找到", "不知道", "无法回答")
        refused = any(marker in answer for marker in refusal_markers)
        if case.is_unanswerable:
            quality = 1.0 if refused else 0.0
        else:
            quality = 0.0 if (not has_evidence and answer and not refused) else 1.0
        return {"abstention_quality": quality, "refused": refused, "has_evidence": has_evidence}, {
            "unanswerable": bool(case.is_unanswerable)
        }


class RagasEvaluator:
    name = "ragas"

    def __init__(self, manifest: dict[str, Any] | None = None, settings: Any | None = None) -> None:
        self.manifest = manifest or {}
        self.settings = settings

    def evaluate(self, case: Any, trace: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        tool_version = "unavailable"
        try:
            import ragas  # type: ignore

            tool_version = str(getattr(ragas, "__version__", "unknown"))
        except ImportError:
            return {"status": "skipped", "reason": "RAGAS_NOT_INSTALLED", "tool_version": tool_version}, {}
        base_url = self.manifest.get("judge_base_url") or getattr(self.settings, "evaluation_judge_base_url", None)
        configured_key = getattr(self.settings, "evaluation_judge_api_key", None)
        api_key = self.manifest.get("judge_api_key") or (configured_key.get_secret_value() if configured_key else None)
        model = self.manifest.get("judge_model") or getattr(self.settings, "evaluation_judge_model", None)
        if not base_url or not api_key or not model:
            return {"status": "skipped", "reason": "RAGAS_PROVIDER_NOT_CONFIGURED", "tool_version": tool_version, "judge_model": model, "prompt_version": self.manifest.get("judge_prompt_version")}, {}
        contexts = [str(x.get("content", "")) for x in (trace.retrieval_snapshot or {}).get("candidates", []) if x.get("content")]
        answer = str((trace.output_snapshot or {}).get("answer") or "")
        try:
            from ragas import EvaluationDataset, evaluate
            from ragas.dataset_schema import SingleTurnSample
            from ragas.metrics import AnswerCorrectness, ContextPrecision, ContextRecall, Faithfulness, ResponseRelevancy
            from ragas.llms import LangchainLLMWrapper
            from ragas.embeddings import LangchainEmbeddingsWrapper
            from langchain_openai import ChatOpenAI, OpenAIEmbeddings

            llm = ChatOpenAI(model=model, api_key=api_key, base_url=str(base_url).rstrip("/"), temperature=0)
            # RAGAS metrics such as response relevancy and answer correctness
            # require embeddings. Use the project's Qwen embedding provider
            # explicitly instead of RAGAS's implicit OpenAI default.
            embedding_key = self.manifest.get("embedding_api_key") or self.settings.qwen_embedding_api_key.get_secret_value()
            embedding_base_url = self.manifest.get("embedding_base_url") or self.settings.qwen_embedding_base_url
            embedding_model = self.manifest.get("embedding_model") or self.settings.qwen_embedding_model
            embeddings = OpenAIEmbeddings(
                model=embedding_model,
                api_key=embedding_key,
                base_url=str(embedding_base_url).rstrip("/"),
                # DashScope's compatible endpoint expects strings in input;
                # disable LangChain's tiktoken token-id payload.
                tiktoken_enabled=False,
                check_embedding_ctx_length=False,
            )
            ragas_llm = LangchainLLMWrapper(llm)
            ragas_embeddings = LangchainEmbeddingsWrapper(embeddings)
            sample = SingleTurnSample(user_input=case.question, retrieved_contexts=contexts, response=answer, reference=case.reference_answer or "")
            dataset = EvaluationDataset(samples=[sample])
            metric_specs = [
                ("faithfulness", lambda: Faithfulness(llm=ragas_llm)),
                ("answer_relevancy", lambda: ResponseRelevancy(llm=ragas_llm, embeddings=ragas_embeddings)),
                ("context_precision", lambda: ContextPrecision(llm=ragas_llm)),
                ("context_recall", lambda: ContextRecall(llm=ragas_llm)),
                ("answer_correctness", lambda: AnswerCorrectness(llm=ragas_llm, embeddings=ragas_embeddings)),
            ]
            scores: dict[str, float] = {}
            metric_errors: dict[str, str] = {}
            # Run metrics independently so one provider 400 does not turn all
            # scores into NaN, and so the failing metric is identifiable.
            for metric_name, metric_factory in metric_specs:
                try:
                    metric = metric_factory()
                    result = evaluate(
                        dataset,
                        metrics=[metric],
                        llm=ragas_llm,
                        embeddings=ragas_embeddings,
                        raise_exceptions=True,
                        show_progress=False,
                    )
                    value = result.to_pandas().iloc[0].get(metric.name)
                    if isinstance(value, (int, float)) and math.isfinite(float(value)):
                        scores[metric_name] = float(value)
                    else:
                        metric_errors[metric_name] = "RAGAS returned a non-finite score"
                except Exception as exc:
                    detail = str(exc)
                    response = getattr(exc, "response", None)
                    if response is not None:
                        try:
                            detail = f"{detail}; provider_response={response.text}"
                        except Exception:
                            pass
                    metric_errors[metric_name] = detail[:2000]
            status = "completed" if not metric_errors else ("partial" if scores else "failed")
            payload: dict[str, Any] = {
                "status": status,
                "tool_version": tool_version,
                "judge_model": model,
                "prompt_version": self.manifest.get("judge_prompt_version"),
                "scores": scores,
            }
            if metric_errors:
                payload["reason"] = "RAGAS_PARTIAL_FAILURE" if scores else "RAGAS_JUDGE_FAILED"
                payload["metric_errors"] = metric_errors
            return payload, {"context_count": len(contexts)}
        except Exception as exc:
            # Preserve the provider's JSON error body when available. The
            # OpenAI SDK often renders only "400 Bad Request" in str(exc),
            # hiding the actionable message (unsupported response_format,
            # invalid parameter, etc.).
            detail = str(exc)
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    detail = f"{detail}; provider_response={response.text}"
                except Exception:
                    pass
            return {"status": "failed", "reason": "RAGAS_JUDGE_FAILED", "detail": detail[:2000], "tool_version": tool_version, "judge_model": model, "prompt_version": self.manifest.get("judge_prompt_version")}, {"context_count": len(contexts)}
