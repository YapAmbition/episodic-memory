import { CompensationNotice, MigrationRecovery, type Compensation } from './MigrationRecovery';
import { SpaceTransfer } from './SpaceTransfer';
import { useState } from 'react';
import { ArrowRight, Copy } from 'lucide-react';
import { post } from './api';
import type { Space } from './types';
import './spaceMerge.css';

type Choice = 'skip'|'overwrite';
type Conflict = {source_id:string;target_id:string;origin_id:string;source_title:string;target_title:string;target_frozen:boolean;source_version:number;target_version:number};
type Preview = {compensation?:Compensation;preview_token:string; counts:Record<string,number>; reused_people:number; new_events:number; conflicts:Conflict[]; pinned_events:{id:string;title:string;from_source:boolean}[]; selected_pins:string[];pin_limit:number};
export function SpaceMerge({spaces,onMerged}:{spaces:Space[];onMerged:(target:string)=>Promise<void>}) {
  const [tab,setTab]=useState<'copy'|'transfer'>('copy');
  const [source,setSource]=useState(''),[target,setTarget]=useState('');
  const [preview,setPreview]=useState<Preview|null>(null),[pins,setPins]=useState<string[]>([]);
  const [choices,setChoices]=useState<Record<string,Choice>>({});
  const [confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  function reset(){setPreview(null);setConfirmed(false);setError('');setNotice('');setChoices({})}
  async function inspect(){setBusy(true);setError('');setNotice('');setPreview(null);setConfirmed(false);try{
    const p=await post<Preview>(`/spaces/${source}/copy/preview`,{target_space_id:target});
    setPreview(p);setPins(p.selected_pins);setChoices({});
  }catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
  async function copy(){if(!preview)return;setBusy(true);setError('');try{
    const result=await post<{counts:Record<string,number>;compensation?:Compensation}>(`/spaces/${source}/copy`,{target_space_id:target,preview_token:preview.preview_token,event_choices:choices,pinned_event_ids:pins});
    setPreview(null);setConfirmed(false);setNotice(`复制完成：新增 ${result.counts.create||0} 件、覆盖 ${result.counts.overwrite||0} 件、跳过 ${result.counts.skip||0} 件事件。两个空间仍可独立使用。补偿资料 ${result.compensation?.compensated||0} 条，待恢复 ${result.compensation?.pending||0} 条。`);await onMerged(target);
  }catch(e){setError(e instanceof Error?e.message:String(e));setConfirmed(false)}finally{setBusy(false)}}
  const overwritten=preview?.conflicts.filter(c=>choices[c.source_id]==='overwrite').length||0;
  return <><div className="transfer-tabs"><button className={tab==='copy'?'active':''} onClick={()=>setTab('copy')}>本机空间复制</button><button className={tab==='transfer'?'active':''} onClick={()=>setTab('transfer')}>文件导入 / 导出</button></div>{tab==='transfer'?<SpaceTransfer spaces={spaces} onImported={onMerged}/>:<section className="merge-page">
    <div className="page-heading"><div><div className="eyebrow">MEMORY SLICE · COPY</div><h1>复制记忆，各自继续。</h1><p>复制事件、关联与聊天记录。源空间完整保留，之后的修改互不影响。</p></div><Copy size={28}/></div>
    <div className="merge-grid"><div className="merge-card">
      <h2>选择复制方向</h2>
      <label>源空间 · 完整保留<select aria-label="源空间" disabled={busy} value={source} onChange={e=>{setSource(e.target.value);reset()}}><option value="">请选择</option>{spaces.map(s=><option key={s.id} value={s.id} disabled={s.id===target}>{s.name}</option>)}</select></label>
      <ArrowRight size={18}/>
      <label>目标空间 · 接收副本<select aria-label="目标空间" disabled={busy} value={target} onChange={e=>{setTarget(e.target.value);reset()}}><option value="">请选择</option>{spaces.map(s=><option key={s.id} value={s.id} disabled={s.id===source}>{s.name}</option>)}</select></label>
      <p>双方的配置、关注对象、聊天接入和同步进度各自保留。同平台与 UID、已有身份映射或相同来源的人物复用目标记录；不按姓名自动合并。</p>
      <button className="button" disabled={busy||!source||!target||source===target} onClick={()=>void inspect()}>{busy?'处理中…':'预览复制'}</button>
    </div><div className="merge-card">
      <h2>核对复制内容</h2>
      {!preview?<p>选择两个空间并预览。重复复制会识别同源记录，聊天消息不会重复添加。</p>:<>
        <CompensationNotice value={preview.compensation}/><div className="merge-counts">{[['event','源事件'],['message','源消息'],['conversation','源会话'],['person','源人物']].map(([key,label])=><div key={key}><strong>{preview.counts[key]||0}</strong><span>{label}</span></div>)}</div>
        <p>新增 {preview.new_events} 件事件，发现 {preview.conflicts.length} 件同源事件。{preview.reused_people} 个人物复用目标记录。</p>
        {!!preview.conflicts.length&&<div className="copy-conflicts"><h3>同源事件 · 默认跳过</h3><div className="copy-batch"><button className="button" disabled={busy} onClick={()=>{setChoices({});setConfirmed(false)}}>全部跳过</button><button className="button" disabled={busy} onClick={()=>{setChoices(Object.fromEntries(preview.conflicts.map(c=>[c.source_id,c.target_frozen?'skip':'overwrite'])));setConfirmed(false)}}>覆盖未冻结事件</button></div>
          {preview.conflicts.map(c=><label className="copy-conflict" key={c.source_id}><span><strong>{c.source_title}</strong><small>目标：{c.target_title}{c.target_frozen?' · 已冻结，请先解冻才能覆盖':''}</small></span><select aria-label={`处理 ${c.source_title}`} disabled={busy} value={choices[c.source_id]||'skip'} onChange={e=>{setChoices({...choices,[c.source_id]:e.target.value as Choice});setConfirmed(false)}}><option value="skip">跳过 · 保留目标</option><option value="overwrite" disabled={c.target_frozen}>覆盖 · 使用源内容</option></select></label>)}
          <p>将覆盖 {overwritten} 件、跳过 {preview.conflicts.length-overwritten} 件。覆盖前保存目标旧版本，目标事件 ID 保持不变。</p>
        </div>}
        {preview.pinned_events.length>0&&<><h3>目标空间的常驻选择</h3><p>默认保留目标常驻。可选择源空间的常驻副本，去重后最多 3 件，总上限 {preview.pin_limit.toLocaleString()} 字符，提交时会按复制后的内容校验。</p>{preview.pinned_events.map(e=><label className="merge-confirm" key={e.id}><input type="checkbox" disabled={busy} checked={pins.includes(e.id)} onChange={v=>{setPins(v.target.checked?[...pins,e.id]:pins.filter(id=>id!==e.id));setConfirmed(false)}}/>{e.title} · {e.from_source?'来自源空间':'目标已常驻'}</label>)}</>}
        <div className="merge-note">复制不删除源空间，也不会同步之后的修改。不会请求大模型。覆盖不会自动合并双方正文，目标旧内容保存在版本历史中。</div>
        <label className="merge-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已核对复制方向和同源事件处理方式。</label>
        <button className="button primary" disabled={busy||!confirmed} onClick={()=>void copy()}>确认复制</button>
      </>}
    </div></div>
    <MigrationRecovery spaceId={target}/>
    {(error||notice)&&<div role="status" className={`merge-feedback ${error?'error':''}`}>{error||notice}</div>}
  </section>}</>
}
