import { useEffect, useState } from 'react';
import { api, post, put } from './api';

type Status = { enabled: boolean; since: number | null; space_id: string | null; status: string; last_success: number | null; last_error: string | null; imported_messages: number; pending_turns: number; tracked_threads: number; excluded_threads: string[]; excluded_projects: string[]; threads: {id:string;title:string}[] };
export function CodexSyncPanel({spaceId}: {spaceId:string}) {
  const [state,setState]=useState<Status|null>(null), [error,setError]=useState('');
  const [thread,setThread]=useState(''), [from,setFrom]=useState(''), [until,setUntil]=useState('');
  const [excluded,setExcluded]=useState(''), [projects,setProjects]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(()=>{let live=true;const load=async()=>{try{const v=await api<Status>('/integrations/codex');if(live){setState(v);setError('');}}catch(e){if(live)setError(String(e));}};load();const timer=setInterval(load,5000);return()=>{live=false;clearInterval(timer);};},[]);
  useEffect(()=>{if(state){setExcluded(state.excluded_threads.join('\n'));setProjects(state.excluded_projects.join('\n'));}},[state?.excluded_threads.join('\n'),state?.excluded_projects.join('\n')]);
  async function action(fn:()=>Promise<unknown>){setBusy(true);try{await fn();setState(await api<Status>('/integrations/codex'));setError('');}catch(e){setError(String(e));}finally{setBusy(false);}}
  if(state?.space_id && state.space_id!==spaceId)return null;
  return <section className="panel" style={{padding:'20px',marginBottom:'20px'}}>
    <strong>Codex 对话同步</strong>
    <p className="subtle-note">每 15 秒检查正式用户会话，只保存用户文字与助手最终回复；不保存思考、工具输出或内部任务提示词。进行中的回合结束后同步。</p>
    {state && <><p>状态：{({idle:'已同步',syncing:'同步中',waiting:'等待同步',disabled:'已暂停',error:'同步失败'} as Record<string,string>)[state.status] || state.status} · 已导入 {state.imported_messages} 条 · 待处理 {state.pending_turns} 轮</p>
      <p className="subtle-note">默认起点：{state.since?new Date(state.since*1000).toLocaleString():'尚未启用'} · 最近成功：{state.last_success?new Date(state.last_success*1000).toLocaleString():'暂无'}。旧会话继续聊，只记录起点之后的新消息。</p>
      <div className="toolbar-group"><button className="button secondary" disabled={busy} onClick={()=>action(()=>put('/integrations/codex',{space_id:spaceId,enabled:!state.enabled}))}>{state.enabled?'暂停同步':'启用同步'}</button><button className="button secondary" disabled={busy||!state.enabled} onClick={()=>action(()=>post('/integrations/codex/poll',{}))}>立即检查</button></div>
      <details style={{marginTop:12}}><summary>采集范围与指定历史补录</summary>
        <p className="subtle-note">每行一个排除项。暂停再启用不会重置采集起点；排除不会删除已保存的记忆。</p>
        <label>排除的 Codex 会话 ID<textarea aria-label="排除会话" value={excluded} onChange={e=>setExcluded(e.target.value)} /></label>
        <label>排除的项目目录（完整路径）<textarea aria-label="排除项目" value={projects} onChange={e=>setProjects(e.target.value)} /></label>
        <button className="button secondary" disabled={busy} onClick={()=>action(()=>put('/integrations/codex',{excluded_threads:excluded.split('\n').map(s=>s.trim()).filter(Boolean),excluded_projects:projects.split('\n').map(s=>s.trim()).filter(Boolean)}))}>保存范围</button>
        <p>历史补录仅在你明确选择后执行，不会自动导入过去的聊天。</p>
        <input aria-label="补录会话 ID" placeholder="Codex 会话 ID" value={thread} list="codex-sync-threads" onChange={e=>setThread(e.target.value)}/>
        <datalist id="codex-sync-threads">{state.threads.map(t=><option key={t.id} value={t.id}>{t.title}</option>)}</datalist>
        <label>从 <input type="datetime-local" value={from} onChange={e=>setFrom(e.target.value)}/></label>
        <label>到 <input type="datetime-local" value={until} onChange={e=>setUntil(e.target.value)}/></label>
        <button className="button secondary" disabled={busy||!thread||!from||!until||!state.enabled} onClick={()=>action(()=>post('/integrations/codex/backfill',{thread_id:thread,since:new Date(from).toISOString(),until:new Date(until).toISOString()}))}>补录所选时间段</button>
      </details></>}
    {(error||state?.last_error)&&<p role="alert">{error||state?.last_error}</p>}
  </section>;
}
