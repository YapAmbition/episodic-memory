import { useEffect, useState } from 'react';
import { Download, FlaskConical, Loader2, Play, CheckCircle2 } from 'lucide-react';
import { api, post } from './api';
import './recallBenchmark.css';

type Summary = { cases:number; 'hit@5':number|null; 'recall@5':number|null; 'mrr@5':number|null; negative_false_positive_rate:number|null; latency_mean_ms:number; degraded_cases:number };
type Row = { id:string; suite:string; query:string; mode:string; expected:string[]; hits:string[]; missing:string[]; selected:string[]; elapsed_ms:number; degraded:boolean; regressed:boolean; forbidden_hits:string[] };
type Report = { events:Record<string,{title:string;body:string}>; summaries:Record<string,Record<string,Summary>>; results:Row[]; baseline:Record<string,Summary>|null; embedding:{model:string}|null };
type Job = {id:string; space_id:string; mode:string; status:string; phase:string; completed:number;total:number;created_at:string; error:string|null;report:Report|null};
const names:Record<string,string> = {all:'全部问题',lexical:'关键词与细节',semantic:'语义改写',graph:'关联事件',context:'连续追问',negative:'无答案与边界'};
const pct=(v:number|null|undefined)=>v==null?'—':`${(v*100).toFixed(1)}%`;
const failed=(r:Row)=>r.missing.length>0 || r.forbidden_hits.length>0 || (!r.expected.length && r.selected.length>0);

export function RecallBenchmark({sid}:{sid:string}) {
  const [jobs,setJobs]=useState<Job[]>([]),[chosen,setChosen]=useState(''),[mode,setMode]=useState('local'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[filter,setFilter]=useState('all');
  useEffect(()=>{ let live=true; const load=async()=>{try{const rows=await api<Job[]>('/benchmarks');if(live){setJobs(rows);}}catch(e){if(live)setError(String(e));}};void load();const timer=setInterval(()=>void load(),1500);return()=>{live=false;clearInterval(timer);};},[]);
  const job=jobs.find(j=>j.id===chosen)||jobs.at(-1), report=job?.report;
  const running=busy||jobs.some(j=>j.status==='running');
  async function start(){setBusy(true);setError('');try{const j=await post<Job>(`/spaces/${sid}/benchmarks?mode=${mode}`,{});setChosen(j.id);setJobs(old=>[...old.filter(x=>x.id!==j.id),j]);}catch(e){setError(String(e));}finally{setBusy(false);}}
  function download(){if(!job)return;const u=URL.createObjectURL(new Blob([JSON.stringify(job,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download=`${job.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}
  const rows=report?.results.filter(r=>filter==='all'||(filter==='failed'?failed(r):filter==='regressed'?r.regressed:r.suite===filter))||[];
  return <section className="recall-benchmark">
    <div className="rb-hero"><div className="rb-eyebrow"><FlaskConical size={16}/> RECALL BENCHMARK</div><h2>每次修改，都有一把相同的尺子。</h2><p>16 件固定记忆 · 28 个问题 · 5 组场景。每轮使用新的临时数据库，日常记忆不会被读取或改变。</p></div>
    <div className="rb-controls"><label>测试方式<select value={mode} disabled={running} onChange={e=>setMode(e.target.value)}><option value="local">程序召回 · 不联网</option><option value="both">程序 + Embedding 对照</option></select></label><button className="button primary" onClick={()=>void start()} disabled={running||!sid}>{running?<Loader2 size={17} className="spin"/>:<Play size={17}/>} {running?'测试运行中':'开始测试'}</button><span>{mode==='both'?'使用当前空间的 Embedding 配置；固定测试事件和问题会发往该服务，产生调用费用。':'无需配置模型。通常几秒完成，自动与固定程序基线对比。'}</span></div>
    {error&&<p role="alert" className="rb-error">{error}</p>}
    {job?.status==='running'&&<div className="rb-progress" role="status"><span>{job.phase==='indexing'?'正在建立独立向量索引':`正在测试 ${job.completed} / ${job.total}`}</span><progress max={job.total} value={job.completed}/></div>}
    {job?.error&&<p role="alert" className="rb-error">{job.error}</p>}
    <div className="rb-history"><label>本次服务运行期间的测试<select value={job?.id||''} onChange={e=>setChosen(e.target.value)}><option value="" disabled>尚未运行</option>{[...jobs].reverse().map(j=><option key={j.id} value={j.id}>{new Date(j.created_at).toLocaleString()} · {j.mode==='local'?'程序':'对照'} · {{running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消'}[j.status]||j.status}</option>)}</select></label><button className="button" disabled={!report} onClick={download}><Download size={15}/>导出结果</button></div>
    {!report&&!running&&<div className="rb-empty"><FlaskConical size={36}/><h3>先跑一次，建立直观印象</h3><p>查看哪些问题被找回、哪些漏掉，以及无答案时有没有误召回。结果可导出，服务重启后列表会清空。</p></div>}
    {report&&<><div className="rb-scorecards">{Object.entries(report.summaries).map(([m,s])=><article key={m}><div className="rb-card-title">{m==='local'?'程序召回':`混合召回 · ${report.embedding?.model||''}`}<CheckCircle2 size={17}/></div><strong>{pct(s.all['recall@5'])}</strong><span>Recall@5 · 预期事件覆盖率</span><div className="rb-mini"><div>问题命中率<b>{pct(s.all['hit@5'])}</b></div><div>无答案误召回率<b>{pct(s.all.negative_false_positive_rate)}</b></div><div>平均耗时<b>{s.all.latency_mean_ms.toFixed(0)} ms</b></div><div>降级问题<b>{s.all.degraded_cases}</b></div></div>{m==='local'&&report.baseline&&<p>固定基线 {pct(report.baseline.all['recall@5'])} · 变化 {((s.all['recall@5']!-report.baseline.all['recall@5']!)*100).toFixed(1)} 个百分点</p>}</article>)}</div>
    <div className="rb-table-wrap"><table><caption>按场景查看效果</caption><thead><tr><th>场景</th><th>方式</th><th>问题数</th><th>Recall@5</th><th>首次命中排名得分</th><th>无答案误召回率</th></tr></thead><tbody>{Object.entries(report.summaries).flatMap(([m,ss])=>Object.entries(ss).filter(([n])=>n!=='all').map(([n,s])=><tr key={`${m}-${n}`}><td>{names[n]}</td><td>{m==='local'?'程序':'混合'}</td><td>{s.cases}</td><td>{pct(s['recall@5'])}</td><td>{s['mrr@5']?.toFixed(2)??'—'}</td><td>{pct(s.negative_false_positive_rate)}</td></tr>))}</tbody></table></div>
    <div className="rb-results-head"><h3>逐题核对 <small>{rows.length} 条</small></h3><select aria-label="筛选测试结果" value={filter} onChange={e=>setFilter(e.target.value)}><option value="all">全部结果</option><option value="failed">未完全命中 / 误召回</option><option value="regressed">相比基线退步</option>{Object.entries(names).filter(([k])=>k!=='all').map(([k,n])=><option value={k} key={k}>{n}</option>)}</select></div>
    <div className="rb-results">{rows.map(r=><details key={`${r.mode}-${r.id}`}><summary><span className={`rb-badge ${failed(r)?'miss':'hit'}`}>{failed(r)?'需关注':'通过'}</span><span className="rb-question">{r.query}<small>{names[r.suite]} · {r.mode==='local'?'程序':'混合'} · {r.elapsed_ms.toFixed(0)} ms {r.degraded?' · 已降级':''} {r.regressed?' · 相比基线退步':''}</small></span><span>{r.expected.length?`${r.hits.length}/${r.expected.length}`:'无答案'}</span></summary><div className="rb-detail"><p>预期事件：{r.expected.map(id=>report.events?.[id]?.title||id).join('、')||'不应返回任何事件'}</p><p>实际返回（排名顺序）：{r.selected.map(id=>report.events?.[id]?.title||id).join(' → ')||'无'}</p><p>漏掉：{r.missing.map(id=>report.events?.[id]?.title||id).join('、')||'无'}；禁止项：{r.forbidden_hits.join('、')||'无'}</p>{[...new Set([...r.expected,...r.selected])].map(id=><p key={id}><b>{report.events?.[id]?.title||id}</b><br/>{report.events?.[id]?.body}</p>)}</div></details>)}{!rows.length&&<p>没有符合筛选条件的结果。</p>}</div>
    <p className="rb-footnote">Recall@5 统计前 5 个输出事件的覆盖率；无答案误召回检查全部输出。固定小样本用于比较改动，不代表真实聊天总体准确率，也不评估记忆整理或最终回答质量。</p></>}
  </section>;
}
