import { CompensationNotice, MigrationRecovery, type Compensation } from './MigrationRecovery';
import { useState } from 'react';
import { Download, Upload } from 'lucide-react';
import { api, post, downloadMemory } from './api';
import type { Space } from './types';

type Choice='skip'|'overwrite';
type Preview={compensation?:Compensation;preview_token:string;target:{name:string};counts:Record<string,number>;new_events:number;conflicts:{source_id:string;source_title:string;target_title:string;target_frozen:boolean}[];pinned_events:{id:string;title:string;from_source:boolean}[];selected_pins:string[];pin_limit:number;restorable_messages:number};
type Result={compensation?:Compensation;space_id:string;space_name:string;counts:Record<string,number>;backup_path:string;operation_id:string};
const pendingKey='memory-slice-last-import';
export function SpaceTransfer({spaces,onImported}:{spaces:Space[];onImported:(sid:string)=>Promise<void>}){
 const [source,setSource]=useState(''),[target,setTarget]=useState('');
 const [name,setName]=useState(''),[file,setFile]=useState<{name:string;package:Record<string,unknown>}|null>(null);
 const [operation,setOperation]=useState(''),[preview,setPreview]=useState<Preview|null>(null),[choices,setChoices]=useState<Record<string,Choice>>({}),[pins,setPins]=useState<string[]>([]);
 const [busy,setBusy]=useState(false),[confirmed,setConfirmed]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<Result|null>(null);
 const [pending,setPending]=useState(()=>sessionStorage.getItem(pendingKey)||'');
 function reset(){setPreview(null);setConfirmed(false);setError('');setResult(null);setOperation('');setChoices({})}
 async function selectFile(selected?:File){reset();setFile(null);if(!selected)return;try{
  if(selected.size>32*1024*1024)throw Error('文件超过 32 MiB，当前版本暂不支持，请保留源文件与原库');
  const parsed=JSON.parse(await selected.text());
  if(parsed.format!=='memory-slice')throw Error('请选择记忆空间迁移包；旧版 Markdown 导出 ZIP 不能导入');
  setFile({name:selected.name,package:parsed});
 }catch(e){setError(e instanceof Error?e.message:'文件读取失败')}}
 async function download(){setBusy(true);setError('');try{
  await downloadMemory(source);
 }catch(e){setError(String(e))}finally{setBusy(false)}}
 function body(){return {package:file?.package,operation_id:operation,target_space_id:target||null,new_space_name:target?null:name.trim()||null}}
 async function inspect(){setBusy(true);setError('');setResult(null);setConfirmed(false);setPreview(null);try{
  const id=operation || (await post<{operation_id:string}>('/transfers/operation-id',{})).operation_id;setOperation(id);
  const p=await post<Preview>('/transfers/preview',{...body(),operation_id:id});setPreview(p);setChoices({});setPins(p.selected_pins);
 }catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}}
 async function finish(r:Result){setResult(r);setPreview(null);setConfirmed(false);setPending('');sessionStorage.removeItem(pendingKey);setOperation('');await onImported(r.space_id)}
 async function commit(){if(!preview)return;setBusy(true);setError('');sessionStorage.setItem(pendingKey,operation);setPending(operation);try{
  await finish(await post<Result>('/transfers/import',{...body(),preview_token:preview.preview_token,event_choices:choices,pinned_event_ids:pins}));
 }catch(e){setError((e instanceof Error?e.message:String(e))+'。若连接中断，可先查询上次结果；不要急于重复导入。')}finally{setBusy(false)}}
 async function check(){setBusy(true);setError('');try{await finish(await api<Result>(`/transfers/operations/${pending}`))}catch(e){setError('暂未查到成功记录。若请求仍在运行，请稍后再查；否则可以重新预览后导入。'+String(e))}finally{setBusy(false)}}
 return <section className="merge-page">
  <div className="page-heading"><div><div className="eyebrow">MEMORY SLICE · TRANSFER</div><h1>带着记忆，换个地方。</h1><p>独立服务与 AstrBot 共用迁移文件。先校验与预览，备份成功后才写入。</p></div><Upload size={28}/></div>
  <div className="merge-grid"><div className="merge-card"><h2>导出空间</h2>
   <label>选择空间<select aria-label="导出空间" value={source} disabled={busy} onChange={e=>setSource(e.target.value)}><option value="">请选择</option>{spaces.map(s=><option key={s.id} value={s.id}>{s.name} · {s.id.slice(-6)}</option>)}</select></label>
   <p>包含事件、关联、人物、片段、历史版本、冻结状态和常驻候选。不带密钥、模型配置、聊天接入和向量缓存。</p>
   <p>包含现存聊天正文；已按保留策略清理的正文无法恢复。导出不会修改源数据。</p>
   <button className="button primary" disabled={busy||!source} onClick={()=>void download()}><Download size={15}/> 下载迁移文件</button><MigrationRecovery spaceId={source}/>
  </div><div className="merge-card"><h2>导入空间</h2>
   <div className="transfer-file-field"><span>选择迁移文件</span><label className={`transfer-upload ${busy?'disabled':''}`}>
    <input aria-label="迁移文件" type="file" accept=".json,application/json" disabled={busy} onChange={e=>void selectFile(e.target.files?.[0])}/>
    <span className="transfer-upload-icon"><Upload size={20}/></span>
    <span className="transfer-upload-copy"><strong title={file?.name}>{file?file.name:'选择记忆迁移文件'}</strong><small>{file?'文件已选择，可继续校验预览':'JSON 格式 · 最大 32 MiB'}</small></span>
    <span className="transfer-upload-action">{file?'更换文件':'浏览文件'}</span>
   </label></div>
   <label>导入到<select aria-label="导入目标" value={target} disabled={busy} onChange={e=>{setTarget(e.target.value);reset()}}><option value="">创建新空间</option>{spaces.map(s=><option key={s.id} value={s.id}>{s.name} · {s.id.slice(-6)}</option>)}</select></label>
   {!target&&<label>新空间名称（可选）<input aria-label="新空间名称" value={name} maxLength={80} disabled={busy} placeholder="默认沿用文件中的名称，同名自动加后缀" onChange={e=>{setName(e.target.value);reset()}}/></label>}
   <p>AstrBot 迁移通常选择新平台已经创建的空间；创建独立空间不会自动接到群聊。</p>
   <button className="button" disabled={busy||!file} onClick={()=>void inspect()}>校验并预览导入</button>
   {preview&&<><h3>目标：{preview.target.name}</h3><CompensationNotice value={preview.compensation}/><p>可补全旧版省略的聊天正文 {preview.restorable_messages||0} 条；主动清理的正文不会恢复。</p><p>源事件 {preview.counts.event||0} 件，新增 {preview.new_events} 件，同源 {preview.conflicts.length} 件。</p>
    {preview.conflicts.length>0&&<div className="copy-conflicts"><div className="copy-batch"><button className="button" disabled={busy} onClick={()=>{setChoices({});setConfirmed(false)}}>全部跳过</button><button className="button" disabled={busy} onClick={()=>{setChoices(Object.fromEntries(preview.conflicts.map(c=>[c.source_id,c.target_frozen?'skip':'overwrite'])));setConfirmed(false)}}>覆盖未冻结事件</button></div>{preview.conflicts.map(c=><label className="copy-conflict" key={c.source_id}><span><strong>{c.source_title}</strong><small>目标：{c.target_title}{c.target_frozen?' · 已冻结':''}</small></span><select aria-label={`导入处理 ${c.source_title}`} disabled={busy} value={choices[c.source_id]||'skip'} onChange={e=>{setChoices({...choices,[c.source_id]:e.target.value as Choice});setConfirmed(false)}}><option value="skip">跳过 · 保留目标</option><option value="overwrite" disabled={c.target_frozen}>覆盖 · 保留旧版本</option></select></label>)}</div>}
    {!!preview.pinned_events.length&&<><h3>目标常驻选择</h3><p>默认保留目标常驻，去重后最多 3 件，总上限 {preview.pin_limit.toLocaleString()} 字符。</p>{preview.pinned_events.map(p=><label className="merge-confirm" key={p.id}><input type="checkbox" disabled={busy} checked={pins.includes(p.id)} onChange={e=>{setPins(e.target.checked?[...pins,p.id]:pins.filter(id=>id!==p.id));setConfirmed(false)}}/>{p.title} · {p.from_source?'来自文件':'目标已常驻'}</label>)}</>}
    <div className="merge-note">导入前自动备份原数据库。任何写入失败整次回滚；重复事件默认跳过，覆盖保留目标旧版本。配置与聊天接入保留。</div>
    <label className="merge-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已核对目标空间和覆盖选择。</label>
    <button className="button primary" disabled={busy||!confirmed} onClick={()=>void commit()}>{busy?'处理中…':'备份并导入'}</button>
   </>}
  </div></div>
  {pending&&<div className="merge-note">上次导入编号：{pending} <button className="button" disabled={busy} onClick={()=>void check()}>查询上次导入结果</button></div>}
  {error&&<div role="alert" className="transfer-result error">{error}</div>}
  {result&&<div role="status" className="transfer-result"><h3>已导入「{result.space_name}」</h3><CompensationNotice value={result.compensation}/><MigrationRecovery spaceId={result.space_id}/><p>新增 {result.counts.create||0} 件、覆盖 {result.counts.overwrite||0} 件、跳过 {result.counts.skip||0} 件事件。</p><p>导入前备份（服务所在电脑）：<code>{result.backup_path}</code></p><p>如需整库恢复：先停止独立后端或 AstrBot，再按 README 的恢复步骤运行 restore_memory_backup.py。恢复工具会先备份当前数据；恢复整库会回到备份时刻，请保留之后的新聊天。</p></div>}
 </section>
}
