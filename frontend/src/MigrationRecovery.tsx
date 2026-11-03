import { useEffect, useState } from 'react';
import { api } from './api';
export type Compensation = {compensated:number;pending:number;issues:{id:string;kind:string;status:string;reasons:string[]}[];truncated?:boolean};
export function CompensationNotice({value}:{value?:Compensation}) {
  if (!value || !(value.compensated || value.pending)) return null;
  return <div className="merge-note"><strong>补偿资料 {value.compensated} 条 · 待恢复 {value.pending} 条</strong><p>补偿前原始记录完整保留。待恢复资料不参与召回、自动整理或清理。</p>
    <details><summary>查看处理说明{value.truncated?'（前 50 条）':''}</summary>{value.issues.map(x=><p key={x.id}><code>{x.kind}</code> · {x.status==='pending'?'待恢复':'已补偿'}：{x.reasons.join('；')}</p>)}</details>
  </div>;
}
type Recovery = {id:string;record_kind:string;status:string;payload:unknown;issues:string[]};
export function MigrationRecovery({spaceId}:{spaceId:string}) {
  const [data,setData]=useState<{total:number;items:Recovery[]}|null>(null),[error,setError]=useState(''),[offset,setOffset]=useState(0),[busy,setBusy]=useState(false);
  useEffect(()=>{setData(null);setError('');setOffset(0)},[spaceId]);
  async function load(start:number){setBusy(true);setError('');try{const result=await api<{total:number;items:Recovery[]}>(`/spaces/${spaceId}/recovery?offset=${start}&limit=20`);setData(result);setOffset(start)}catch(e){setError(String(e))}finally{setBusy(false)}}
  if (!spaceId) return null;
  return <div className="merge-note"><button className="button" disabled={busy} onClick={()=>void load(0)}>查看此空间的恢复资料</button>
    {error&&<p role="alert">{error}</p>}{data&&<><p>共 {data.total} 条原始资料；这里只读查看，正常聊天与整理不会消费这些资料。</p>{data.items.map(x=><details key={x.id}><summary>{x.record_kind} · {x.status==='pending'?'待恢复':'补偿前原始记录'}</summary><p>{x.issues.join('；')}</p><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:320,overflow:'auto'}}>{JSON.stringify(x.payload,null,2)}</pre></details>)}
    {offset>0&&<button className="button" disabled={busy} onClick={()=>void load(offset-20)}>上一页</button>}{offset+20<data.total&&<button className="button" disabled={busy} onClick={()=>void load(offset+20)}>下一页</button>}</>}
  </div>;
}
