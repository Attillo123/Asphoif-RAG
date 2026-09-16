import { useState } from 'react';
import type { FormEvent } from 'react';

const RUBRIC = 'human-0-5-v1';
const fields = [
  ['answer_correctness', '答案正确性', '事实、结论是否与标准答案及业务规则一致。'],
  ['answer_completeness', '答案完整性', '是否覆盖问题要求的关键要点和适用条件。'],
  ['faithfulness', '答案忠实度', '答案中的事实是否有检索证据支持，是否存在编造。'],
  ['citation_correctness', '引用正确性', '引用是否支持对应结论，是否张冠李戴。'],
  ['citation_completeness', '引用完整性', '需要证据的关键结论是否都有引用。'],
  ['overall', '总体评分', '综合判断答案是否满足用户需求；不是各项分数的自动平均。'],
] as const;
type ScoreKey = typeof fields[number][0];
type Scores = Partial<Record<ScoreKey, number>>;
type Review = { scores: Scores; note?: string | null; reviewer_id?: string; reviewed_at?: string; rubric_version?: string };
export type ReviewCase = { id: string; question: string; reference_answer?: string | null; requires_citation: boolean; is_unanswerable: boolean };
export type ReviewResult = {
  id: string; run_id: string; case_id: string; status: string; request_id?: string;
  answer?: string | null;
  context_snapshot?: { candidates?: { chunk_id?: string; content?: string }[] };
  citations?: { chunk_id?: string; file_name?: string }[];
  metrics?: { human_review?: Review; ragas?: { status?: string; reason?: string; detail?: string; judge_model?: string; scores?: Record<string, number | null>; metric_errors?: Record<string, string> } };
  error_message?: string | null;
};
type ReviewPayload = { scores: Scores; reviewer_note: string | null; rubric_version: string };
const grades = ['完全不满足', '严重问题', '较多问题', '基本满足，仍需修改', '良好，仅有轻微问题', '完全满足'];

export function HumanReviewCard({ item, caseData, save }: {
  item: ReviewResult;
  caseData?: ReviewCase;
  save: (payload: ReviewPayload) => Promise<{ human_review: Review }>;
}) {
  const [saved, setSaved] = useState(item.metrics?.human_review);
  const [scores, setScores] = useState<Scores>(() => Object.fromEntries(fields.flatMap(([key]) => {
    const value = item.metrics?.human_review?.scores?.[key];
    return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 5 ? [[key, value]] : [];
  })));
  const [note, setNote] = useState(saved?.note ?? '');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const complete = fields.every(([key]) => scores[key] !== undefined);
  const canReview = Boolean(item.request_id && item.answer?.trim());
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!complete || !canReview || busy) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const response = await save({ scores, reviewer_note: note.trim() || null, rubric_version: RUBRIC });
      setSaved(response.human_review); setMessage('人工评分已保存');
    } catch (err) { setError(err instanceof Error ? err.message : '保存失败，请重试'); }
    finally { setBusy(false); }
  };
  return <article className="review-card">
    <div className="review-card-heading"><strong>Case · {item.case_id}</strong><span className="status-pill">{saved ? '已评审' : '待评审'}</span></div>
    <div className="review-evidence">
      <section><h3>问题</h3><p>{caseData?.question || '未获取到问题，请重新打开结果'}</p></section>
      <section><h3>标准答案</h3><p>{caseData?.reference_answer || '未提供标准答案，请结合业务规则及证据复核'}</p></section>
      <section className="review-answer"><h3>生成答案</h3><p>{item.answer || '暂无答案'}</p></section>
      <details><summary>检索证据与引用</summary>
        {item.context_snapshot?.candidates?.length ? item.context_snapshot.candidates.map((chunk, index) => <section key={`${chunk.chunk_id}-${index}`}><h3>证据 {index + 1} · {chunk.chunk_id}</h3><p>{chunk.content || '该 Trace 未保存证据正文'}</p></section>) : <p>无检索证据</p>}
        <h3>答案引用</h3>{item.citations?.length ? <ul>{item.citations.map((citation, index) => <li key={`${citation.chunk_id}-${index}`}>{citation.file_name || '来源'} · {citation.chunk_id || '无 Chunk ID'}</li>)}</ul> : <p>无引用</p>}
      </details>
      {item.metrics?.ragas && <section className="case-ragas"><h3>RAGAS + Judge LLM</h3><p>状态：{item.metrics.ragas.status || 'unknown'}{item.metrics.ragas.reason ? ` · ${item.metrics.ragas.reason}` : ''}{item.metrics.ragas.judge_model ? ` · ${item.metrics.ragas.judge_model}` : ''}</p>{item.metrics.ragas.detail && <p className="error">错误详情：{item.metrics.ragas.detail}</p>}{item.metrics.ragas.metric_errors && <div className="case-ragas-errors">{Object.entries(item.metrics.ragas.metric_errors).map(([key, value]) => <p key={key}><b>{key}</b>：{value}</p>)}</div>}{item.metrics.ragas.scores && <div className="case-ragas-scores">{Object.entries(item.metrics.ragas.scores).map(([key, value]) => <span key={key}><b>{key}</b> {typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : '未返回'}</span>)}</div>}</section>}
    </div>
    <form className="review-form" onSubmit={submit}>
      <strong>人工评分 · 0～5 分</strong>
      <p className="review-rubric">0 完全不满足 · 1 严重问题 · 2 较多问题 · 3 基本满足 · 4 良好 · 5 完全满足。未评分不等于 0 分。</p>
      <p className="review-rubric">无需引用且确实没有待支撑结论时，两项引用评分选 5；存在无依据结论或错误引用时仍需扣分，并在备注说明。不可回答问题应结合拒答是否合理评分。</p>
      {caseData && <p className="review-rubric">本 Case：{caseData.requires_citation ? '要求引用' : '不强制引用'} · {caseData.is_unanswerable ? '不可回答问题' : '可回答问题'}</p>}
      {!canReview && <p className="alert">{item.error_message === 'TRACE_NOT_BOUND' ? '该 Case 尚未绑定线上 Trace。请先用相同问题完成一次 Chat，再重新创建评估 Run。' : '该结果缺少 Trace 或生成答案，暂不能进行答案评分。'}</p>}
      <fieldset disabled={busy || !canReview} className="review-score-grid">
        {fields.map(([key, label, help]) => <label key={key}>
          <span>{label}</span><small>{help}</small>
          <select required value={scores[key] ?? ''} onChange={e => { setScores(previous => ({ ...previous, [key]: e.target.value === '' ? undefined : Number(e.target.value) })); setMessage(''); }}>
            <option value="">请选择分数</option>
            {grades.map((grade, score) => <option key={score} value={score}>{score} 分 · {grade}</option>)}
          </select>
        </label>)}
      </fieldset>
      <label className="review-note">评审备注（选填）<textarea disabled={busy || !canReview} maxLength={2000} value={note} onChange={e => { setNote(e.target.value); setMessage(''); }} placeholder="说明错误、遗漏、引用问题或评分依据，最多 2000 字" /></label>
      <button className="primary" disabled={busy || !canReview || !complete}>{busy ? '保存中…' : saved ? '更新人工评分' : '保存人工评分'}</button>
      {saved && <p className="review-rubric">最近评审：{saved.reviewer_id} · {saved.reviewed_at ? new Date(saved.reviewed_at).toLocaleString() : '时间未记录'} · 量表 {saved.rubric_version}。更新将替换该结果的上次人工评分。</p>}
      {message && <p role="status" className="review-success">{message}</p>}
      {error && <p role="alert" className="alert error">{error}</p>}
    </form>
  </article>;
}
