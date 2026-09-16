import { FormEvent, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { HumanReviewCard } from './HumanReviewCard';
import type { ReviewCase, ReviewResult } from './HumanReviewCard';

// Leave the base URL empty in production so the Nginx reverse proxy can keep
// the UI and API on the same origin. A full URL can still be supplied for
// local Vite development through VITE_API_BASE_URL.
const API = import.meta.env.VITE_API_BASE_URL || '';

type User = { id: string; username: string; role: string };
type KnowledgeBase = { id: string; name: string; description?: string; status?: string; current_version?: string };
type Document = { id: string; file_name: string; mime_type: string; status: string; content?: string | null; version?: number | null; content_hash: string };
 type Chunk = { id: string; ordinal: number; content: string; start_offset?: number | null; end_offset?: number | null };
type Job = { ingestion_job_id: string; document_id?: string; status: string; stage?: string; error?: string };
type EventData = Record<string, any>;

const STORAGE_PREFIX = 'asphoif-rag:';

function usePersistentState<T>(key: string, initialValue: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const storageKey = `${STORAGE_PREFIX}${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = localStorage.getItem(storageKey);
      return stored === null ? (typeof initialValue === 'function' ? (initialValue as () => T)() : initialValue) : JSON.parse(stored) as T;
    } catch {
      return typeof initialValue === 'function' ? (initialValue as () => T)() : initialValue;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* storage is best effort */ }
  }, [storageKey, value]);
  return [value, setValue];
}

async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('token');
  const headers: Record<string, string> = { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`${API}${path}`, { ...options, headers: { ...headers, ...(options.headers as Record<string, string> || {}) } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.message || body.error?.message || `请求失败 (${response.status})`);
  return body.data as T;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [form, setForm] = useState({ username: '', password: '' }); const [error, setError] = useState(''); const [loading, setLoading] = useState(false);
  const submit = async (e: FormEvent) => { e.preventDefault(); setLoading(true); setError(''); try { const data = await api<{ access_token: string; user: User }>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify(form) }); localStorage.setItem('token', data.access_token); onLogin(data.user); } catch (err: any) { setError(err.message); } finally { setLoading(false); } };
  return <main className="login"><div className="brand-mark">AR</div><h1>Asphoif RAG</h1><p>企业级 RAG 开发测试控制台</p><form onSubmit={submit}><label>用户名<input required value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></label><label>密码<input required type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label><button disabled={loading}>{loading ? '登录中…' : '登录'}</button></form>{error && <div className="alert error">{error}</div>}</main>;
}

function KnowledgeSidebar({ kbs, selected, onSelect, onRefresh, onCreate, onDelete }: { kbs: KnowledgeBase[]; selected: string; onSelect: (id: string) => void; onRefresh: () => void; onCreate: (name: string) => void; onDelete: (id: string) => void }) {
  const [name, setName] = useState(''); return <aside className="sidebar"><div className="side-title"><h2>知识库</h2><button className="icon-button" onClick={onRefresh}>↻</button></div><div className="kb-list">{kbs.map(kb => <button key={kb.id} className={`kb-item ${selected === kb.id ? 'selected' : ''}`} onClick={() => onSelect(kb.id)}><span className="kb-dot" />{kb.name}</button>)}{!kbs.length && <p className="muted">暂无知识库</p>}</div><div className="create-kb"><input value={name} placeholder="新知识库名称" onChange={e => setName(e.target.value)} /><button onClick={() => { if (name.trim()) { onCreate(name.trim()); setName(''); } }}>创建</button></div>{selected && <button className="danger-button" onClick={() => onDelete(selected)}>删除当前知识库</button>}</aside>;
}

function DocumentsPanel({ kb }: { kb: string }) { const [docs, setDocs] = useState<Document[]>([]); const [doc, setDoc] = useState<Document>(); const [chunks, setChunks] = useState<Chunk[]>([]); const [copied, setCopied] = useState(''); const load = async () => { if (!kb) return; const data = await api<{ items: Document[] }>(`/api/v1/documents?knowledge_base_id=${encodeURIComponent(kb)}`); setDocs(data.items || []); }; useEffect(() => { load(); setDoc(undefined); setChunks([]); }, [kb]); const open = async (id: string) => { const [detail, chunkData] = await Promise.all([api<Document>(`/api/v1/documents/${id}`), api<{ items: Chunk[] }>(`/api/v1/documents/${id}/chunks`)]); setDoc(detail); setChunks(chunkData.items || []); }; const copyId = async (id: string) => { await navigator.clipboard.writeText(id); setCopied(id); window.setTimeout(() => setCopied(''), 1400); }; return <div className="panel"><div className="panel-heading"><div><span className="eyebrow">DOCUMENTS</span><h2>知识库文档</h2></div><button onClick={load}>↻</button></div><div className="documents-layout"><div className="document-list">{!docs.length && <p className="muted">暂无文档</p>}{docs.map(item => <button className={`document-item ${doc?.id === item.id ? 'selected' : ''}`} key={item.id} onClick={() => open(item.id)}><b>{item.file_name}</b><small>{item.mime_type} · {item.status}</small></button>)}</div><div className="document-content">{doc ? <><h3>{doc.file_name}</h3><small>版本 {doc.version ?? '—'} · {chunks.length} 个 Chunk</small><pre>{doc.content || '暂无内容'}</pre><div className="chunk-heading"><h3>Chunk 列表</h3><span className="muted">按入库顺序排列</span></div><div className="chunk-list">{!chunks.length && <p className="muted">暂无 Chunk，文档可能尚未完成入库</p>}{chunks.map(chunk => <article className="chunk-card" key={chunk.id}><div className="chunk-meta"><b>#{chunk.ordinal + 1}</b><code>{chunk.id}</code><button onClick={() => copyId(chunk.id)}>{copied === chunk.id ? '已复制' : '复制 ID'}</button></div><p>{chunk.content}</p></article>)}</div></> : <div className="empty"><span>▤</span><p>选择文档查看内容和 Chunk</p></div>}</div></div></div>; }
function ChatPanel({ kb, onTrace }: { kb: string; onTrace: (data: EventData) => void }) {
  const [query, setQuery] = usePersistentState(`chat.query.${kb}`, ''); const [answer, setAnswer] = usePersistentState(`chat.answer.${kb}`, ''); const [citations, setCitations] = usePersistentState<any[]>(`chat.citations.${kb}`, []); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const send = async () => { if (!query.trim() || !kb) return; const current = query.trim(); setAnswer(''); setCitations([]); setError(''); setBusy(true); try { const response = await fetch(`${API}/api/v1/chat/completions`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token')}` }, body: JSON.stringify({ query: current, knowledge_base_id: kb }) }); if (!response.ok || !response.body) throw new Error('Chat 请求失败'); const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; while (true) { const part = await reader.read(); if (part.done) break; buffer += decoder.decode(part.value, { stream: true }); const frames = buffer.split('`n`n'); buffer = frames.pop() || ''; for (const frame of frames) { const event = frame.match(/^event: (.+)$/m)?.[1]; const raw = frame.match(/^data: (.+)$/m)?.[1]; if (!raw) continue; const data = JSON.parse(raw); onTrace({ ...data, event }); if (event === 'delta' && data.text) setAnswer(v => v + data.text); if (event === 'citation') setCitations(v => [...v, data]); if (event === 'error') setError(data.message || '生成失败'); } } } catch (err: any) { setError(err.message); } finally { setBusy(false); } };
  return <div className="panel"><div className="panel-heading"><div><span className="eyebrow">ONLINE QA</span><h2>Chat 流式问答</h2></div><span className={`status-pill ${busy ? 'running' : ''}`}>{busy ? '生成中' : '就绪'}</span></div><div className="answer-box">{answer || <span className="placeholder">输入问题，验证检索、生成与引用链路…</span>}</div>{citations.length > 0 && <div className="citations"><h3>引用来源</h3>{citations.map((c, i) => <div className="citation" key={`${c.chunk_id}-${i}`}><b>[{i + 1}]</b><span>{c.file_name || c.document_id || c.chunk_id}</span><code>{c.chunk_id}</code></div>)}</div>}<textarea value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send(); }} placeholder="例如：退款多久到账？（Ctrl/⌘ + Enter 发送）" /><button className="primary" disabled={busy || !kb || !query.trim()} onClick={send}>{busy ? '生成中…' : '发送问题'}</button>{error && <div className="alert error">{error}</div>}</div>;
}

function UploadPanel({ kb, jobs, onUpload, onProcess }: { kb: string; jobs: Job[]; onUpload: (file: File) => void; onProcess: () => Promise<void> }) { return <div className="panel"><div className="panel-heading"><div><span className="eyebrow">INGESTION</span><h2>文档上传与任务</h2></div><button className="process-button" onClick={onProcess} disabled={!kb}>处理待处理任务</button></div><label className="dropzone"><span className="upload-icon">↑</span><b>选择 Markdown 或 TXT 文件</b><small>上传后创建一个入库任务；任务由独立 Worker 执行解析、切块、Embedding 和索引</small><input type="file" accept=".md,.markdown,.txt,text/plain,text/markdown" disabled={!kb} onChange={e => { const file = e.target.files?.[0]; if (file) onUpload(file); e.currentTarget.value = ''; }} /></label>{!kb && <p className="muted">请先在左侧选择知识库</p>}{kb && <p className="muted">当前知识库最近任务：{jobs.length} 个。若状态长时间保持 PENDING，请启动入库 Worker。</p>}<div className="job-list">{jobs.map(job => <div className="job-card" key={job.ingestion_job_id}><div><b>{job.document_id || '新文档'}</b><code>{job.ingestion_job_id}</code></div><span className={`job-status ${job.status.toLowerCase()}`}>{job.status}{job.stage ? ` · ${job.stage}` : ''}</span>{job.error && <small className="error">{job.error}</small>}</div>)}</div></div>; }

function SearchPanel({ kb, onTrace }: { kb: string; onTrace: (data: EventData) => void }) { const [query, setQuery] = usePersistentState(`search.query.${kb}`, ''); const [result, setResult] = usePersistentState<any | undefined>(`search.result.${kb}`, undefined); const [loading, setLoading] = useState(false); const run = async () => { if (!kb || !query.trim()) return; setLoading(true); try { const data = await api('/api/v1/retrieval/search', { method: 'POST', body: JSON.stringify({ query: query.trim(), knowledge_base_id: kb }) }); setResult(data); onTrace({ event: 'retrieval', ...data }); } catch (e: any) { onTrace({ event: 'error', message: e.message }); } finally { setLoading(false); } }; return <div className="panel"><div className="panel-heading"><div><span className="eyebrow">RETRIEVAL</span><h2>检索调试</h2></div></div><textarea value={query} onChange={e => setQuery(e.target.value)} placeholder="输入检索问题…" /><button className="primary" disabled={!kb || loading || !query.trim()} onClick={run}>{loading ? '检索中…' : '执行检索'}</button>{result && <div className="retrieval-result"><div className="metric-row"><Metric label="状态" value={result.status} /><Metric label="Dense" value={result.dense?.length ?? 0} /><Metric label="Sparse" value={result.sparse?.length ?? 0} /><Metric label="融合" value={result.fused?.length ?? 0} /></div><pre>{JSON.stringify(result, null, 2)}</pre></div>}</div>; }
function Metric({ label, value }: { label: string; value: any }) { return <div className="metric"><span>{label}</span><b>{value}</b></div>; }
function TracePanel({ trace }: { trace?: EventData }) { return <div className="panel"><div className="panel-heading"><div><span className="eyebrow">OBSERVABILITY</span><h2>Trace 专用视图</h2></div></div>{trace ? <><div className="trace-grid"><Metric label="事件" value={trace.event || '—'} /><Metric label="序号" value={trace.seq || '—'} /><Metric label="状态" value={trace.status || '—'} /><Metric label="Trace ID" value={trace.trace_id || '—'} /></div><pre>{JSON.stringify(trace, null, 2)}</pre></> : <div className="empty"><span>◎</span><p>完成一次 Chat 或检索后，这里会显示 Trace 摘要。</p></div>}<p className="muted">后端 Trace 查询接口接入后，此处可扩展为历史 Trace 检索。</p></div>; }

function EvaluationPanel({ kb }: { kb: string }) {
  const [snapshots, setSnapshots] = useState<any[]>([]); const [runs, setRuns] = useState<any[]>([]); const [question, setQuestion] = usePersistentState(`evaluation.question.${kb}`, ''); const [referenceAnswer, setReferenceAnswer] = usePersistentState(`evaluation.referenceAnswer.${kb}`, ''); const [gold, setGold] = usePersistentState(`evaluation.gold.${kb}`, ''); const [selected, setSelected] = usePersistentState(`evaluation.selected.${kb}`, ''); const [results, setResults] = usePersistentState<any | undefined>(`evaluation.results.${kb}`, undefined); const [message, setMessage] = useState(''); const [judgeBase, setJudgeBase] = usePersistentState(`evaluation.judgeBase.${kb}`, ''); const [judgeKey, setJudgeKey] = usePersistentState(`evaluation.judgeKey.${kb}`, ''); const [judgeModel, setJudgeModel] = usePersistentState(`evaluation.judgeModel.${kb}`, ''); const [reviewCases, setReviewCases] = useState<Record<string, ReviewCase>>({});
  const load = async () => { try { const [s, r] = await Promise.all([api<any>('/api/v1/evaluations/snapshots'), api<any>('/api/v1/evaluations/runs')]); setSnapshots(s.items || []); setRuns(r.items || []); } catch (e: any) { setMessage(e.message); } };
  useEffect(() => { load(); }, []);
  const create = async () => { try { const d = await api<any>('/api/v1/evaluations/snapshots', { method:'POST', body:JSON.stringify({ name:'回归数据集', knowledge_base_id:kb || undefined, cases:[{ question, reference_answer:referenceAnswer.trim() || undefined, gold_chunk_ids:gold.split(',').map(x=>x.trim()).filter(Boolean) }] }) }); setSelected(d.id); setQuestion(''); setReferenceAnswer(''); setGold(''); setMessage('快照已创建'); load(); } catch (e: any) { setMessage(e.message); } };
  const publish = async () => { if (selected) { await api(`/api/v1/evaluations/snapshots/${selected}/publish`, { method:'POST' }); setMessage('快照已发布'); load(); } };
  const run = async () => { if (selected) { try { const snapshot = snapshots.find(item => item.id === selected); if (snapshot?.status !== 'published') await api(`/api/v1/evaluations/snapshots/${selected}/publish`, { method:'POST' }); const d = await api<any>('/api/v1/evaluations/runs', { method:'POST', body:JSON.stringify({ snapshot_id:selected, manifest:{ judge_base_url:judgeBase||undefined, judge_api_key:judgeKey||undefined, judge_model:judgeModel||undefined, judge_prompt_version:'ragas-default-v1' } }) }); await api(`/api/v1/evaluations/runs/${d.id}/execute`, { method:'POST' }); setMessage('评估完成'); load(); } catch (e: any) { setMessage(e.message); } } };
  const executeExisting = async (id: string) => { try { await api(`/api/v1/evaluations/runs/${id}/execute`, { method: 'POST' }); setMessage('评估已重新执行'); await load(); await inspect(id); } catch (e: any) { setMessage(e.message); } };
  const inspect = async (id: string) => {
    try {
      const selectedRun = runs.find(run => run.id === id);
      if (!selectedRun) throw new Error('评估运行未找到，请刷新列表');
      const [data, snapshot] = await Promise.all([
        api(`/api/v1/evaluations/runs/${id}/results`),
        api<{ cases: ReviewCase[] }>(`/api/v1/evaluations/snapshots/${selectedRun.snapshot_id}`),
      ]);
      setReviewCases(Object.fromEntries(snapshot.cases.map(item => [item.id, item])));
      setResults(data);
    } catch (error) { setMessage(error instanceof Error ? error.message : '读取结果失败'); }
  };
  return <div className="panel">
    <div className="panel-heading"><div><span className="eyebrow">EVALUATION</span><h2>评估与回归测试</h2></div><button onClick={load}>↻</button></div>
    <p className="muted">请先在 Chat 问答中用相同问题生成线上 Trace；创建 Run 时系统会按当前知识库和问题自动绑定最近一次 Trace。</p>
    <div className="evaluation-layout">
      <div className="eval-config">
        <h3>数据集与 Case</h3>
        <input value={question} onChange={e=>setQuestion(e.target.value)} placeholder="问题"/>
        <input value={referenceAnswer} onChange={e=>setReferenceAnswer(e.target.value)} placeholder="标准答案（用于 RAGAS 和人工评估）"/>
        <input value={gold} onChange={e=>setGold(e.target.value)} placeholder="gold chunk id（逗号分隔）"/>
        <button className="primary" onClick={create} disabled={!question.trim() || !kb}>创建快照</button>
        <select value={selected} onChange={e=>setSelected(e.target.value)}><option value="">选择快照</option>{snapshots.map(s=><option key={s.id} value={s.id}>{s.name} · {s.status}</option>)}</select>
        <button onClick={publish} disabled={!selected}>发布快照</button>
      </div>
      <div className="eval-runs">
        <h3>Judge 配置与评估 Run</h3>
        <input value={judgeBase} onChange={e=>setJudgeBase(e.target.value)} placeholder="Judge Base URL（可选，默认读取.env）"/>
        <input type="password" value={judgeKey} onChange={e=>setJudgeKey(e.target.value)} placeholder="Judge API Key（可选）"/>
        <input value={judgeModel} onChange={e=>setJudgeModel(e.target.value)} placeholder="Judge 模型（可选）"/>
        <button className="primary" onClick={run} disabled={!selected}>创建并执行评估</button>
      </div>
      <div className="eval-results"><div className="results-heading"><h3>已执行评估结果</h3><span className="muted">选择 Run 查看详细指标</span></div><div className="run-list">{runs.map(r=><div className={`run-card ${results?.run_id === r.id ? 'selected' : ''}`} key={r.id}><code>{r.id}</code><span className="job-status">{r.status}</span><div className="run-actions"><button onClick={()=>executeExisting(r.id)}>重新执行</button><button onClick={()=>inspect(r.id)}>结果</button></div></div>)}</div>{results&&<>
        <div className="metric-section"><h3>确定性指标与规则评估</h3><div className="metric-row">{Object.entries(results.aggregate_metrics||{}).filter(([k])=>!k.startsWith('ragas_')).map(([k,v])=><Metric key={k} label={k} value={String(v)}/>)}</div></div>
        <div className="metric-section ragas-section"><h3>RAGAS + Judge LLM</h3>{Object.entries(results.aggregate_metrics||{}).some(([k])=>k.startsWith('ragas_')) ? <div className="metric-row">{Object.entries(results.aggregate_metrics||{}).filter(([k])=>k.startsWith('ragas_')).map(([k,v])=><Metric key={k} label={k.replace(/^ragas_/,'')} value={typeof v === 'number' && Number.isFinite(v) ? v.toFixed(4) : '未返回'} />)}</div> : <p className="muted">当前 Run 没有可展示的 RAGAS 分数。请检查结果中的 RAGAS 状态。</p>}</div>
        {results.items?.map((item: ReviewResult) => <HumanReviewCard key={item.id} item={item} caseData={reviewCases[item.case_id]} save={payload => api(`/api/v1/evaluations/runs/${item.run_id}/results/${item.id}/review`, { method: 'POST', body: JSON.stringify(payload) })} />)}
      </>}</div>
    </div>
    {message&&<p className="muted">{message}</p>}
  </div>;
}
function App() {
  const [user, setUser] = useState<User>();
  const [authChecking, setAuthChecking] = useState(true);
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [kb, setKb] = usePersistentState('selected-knowledge-base', '');
  const [tab, setTab] = usePersistentState('active-tab', 'chat');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [trace, setTrace] = useState<EventData>();
  const [error, setError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) { setAuthChecking(false); return; }
    api<User>('/api/v1/auth/me')
      .then(setUser)
      .catch(() => localStorage.removeItem('token'))
      .finally(() => setAuthChecking(false));
  }, []);

  const load = async () => {
    try {
      const data = await api<{ items: KnowledgeBase[] }>('/api/v1/knowledge-bases');
      setKbs(data.items || []);
      setKb(current => data.items.some(item => item.id === current) ? current : (data.items[0]?.id || ''));
    } catch (e: any) { setError(e.message); }
  };
  const loadJobs = async (knowledgeBaseId: string) => {
    if (!knowledgeBaseId) { setJobs([]); return; }
    try {
      const data = await api<{ items: Job[] }>(`/api/v1/knowledge-bases/${knowledgeBaseId}/ingestion-jobs?limit=50`);
      setJobs(data.items || []);
    } catch (e: any) { setError(e.message); }
  };
  useEffect(() => { if (user) load(); }, [user]);
  useEffect(() => { if (user && kb) loadJobs(kb); }, [user, kb]);

  const pollJob = (job: Job) => {
    let attempts = 0;
    const poll = async () => {
      try {
        const state = await api<Job>(`/api/v1/ingestion-jobs/${job.ingestion_job_id}`);
        setJobs(items => items.map(item => item.ingestion_job_id === job.ingestion_job_id ? { ...item, ...state } : item));
        attempts += 1;
        if (!['READY', 'PUBLISHED', 'FAILED'].includes(state.status) && attempts < 180) window.setTimeout(poll, 2000);
      } catch (e: any) { setError(e.message); }
    };
    void poll();
  };
  const upload = async (file: File) => {
    try {
      const form = new FormData(); form.append('file', file);
      const job = await api<Job>(`/api/v1/knowledge-bases/${kb}/documents`, { method: 'POST', body: form });
      setJobs(items => [job, ...items.filter(item => item.ingestion_job_id !== job.ingestion_job_id)]);
      pollJob(job);
    } catch (e: any) { setError(e.message); }
  };
  const processJobs = async () => {
    if (!kb) return;
    try {
      const data = await api<{ job: Job | null }>(`/api/v1/knowledge-bases/${kb}/ingestion-jobs/process`, { method: 'POST' });
      if (data.job) {
        setJobs(items => items.map(item => item.ingestion_job_id === data.job?.ingestion_job_id ? { ...item, ...data.job } : item));
        pollJob(data.job);
      } else { await loadJobs(kb); }
    } catch (e: any) { setError(e.message); }
  };
  const deleteKb = async (id: string) => {
    if (!window.confirm('确定删除当前知识库吗？')) return;
    try { await api(`/api/v1/knowledge-bases/${id}`, { method: 'DELETE' }); setKb(''); await load(); }
    catch (e: any) { setError(e.message); }
  };
  if (authChecking) return <main className="login"><p>正在恢复登录状态…</p></main>;
  if (!user) return <Login onLogin={setUser} />;
  const tabs = [['chat', 'Chat 问答'], ['upload', '文档入库'], ['documents', '文档浏览'], ['search', '检索调试'], ['trace', 'Trace 观测'], ['evaluation', '评估回归']];
  return <main className="app-shell"><header className="topbar"><div className="logo"><span>AR</span><b>Asphoif RAG</b></div><div className="top-actions"><span>{user.username} · {user.role}</span><button onClick={() => { localStorage.removeItem('token'); setUser(undefined); }}>退出登录</button></div></header><div className="workspace"><KnowledgeSidebar kbs={kbs} selected={kb} onSelect={setKb} onRefresh={load} onDelete={deleteKb} onCreate={async name => { try { await api('/api/v1/knowledge-bases', { method: 'POST', body: JSON.stringify({ name }) }); await load(); } catch (e: any) { setError(e.message); } }} /><section className="content"><div className="content-tabs">{tabs.map(([id, label]) => <button className={tab === id ? 'active' : ''} onClick={() => setTab(id)} key={id}>{label}</button>)}</div><div className="kb-banner">当前知识库：<b>{kbs.find(x => x.id === kb)?.name || '未选择'}</b><span>{kb || '请选择知识库后开始测试'}</span></div>{tab === 'chat' && <ChatPanel kb={kb} onTrace={setTrace} />}{tab === 'upload' && <UploadPanel kb={kb} jobs={jobs} onUpload={upload} onProcess={processJobs} />}{tab === 'documents' && <DocumentsPanel kb={kb} />}{tab === 'search' && <SearchPanel kb={kb} onTrace={setTrace} />}{tab === 'trace' && <TracePanel trace={trace} />}{tab === 'evaluation' && <EvaluationPanel kb={kb} />} {error && <div className="alert error global-error">{error}<button onClick={() => setError('')}>×</button></div>}</section></div></main>;
}
createRoot(document.getElementById('root')!).render(<App />);




















