import {useEffect, useState} from 'react';
import {api, post, put} from './api';
import {routeStates} from './retrievalUi';
import './retrieval.css';

type Settings = {enabled:boolean; base_url:string; model:string; dimensions:number|null; timeout_seconds:number; failure_threshold:number; cooldown_seconds:number; min_similarity:number; has_key:boolean; indexed_chunks:number; pending_chunks:number; last_error:string|null; circuit_open:boolean; retry_after_seconds:number};
export function EmbeddingSettings({sid, readOnly=false, onError}: {sid:string; readOnly?:boolean; onError:(e:unknown)=>void}) {
 const [status,setStatus]=useState<Settings|null>(null), [draft,setDraft]=useState<Settings|null>(null), [key,setKey]=useState(''), [busy,setBusy]=useState(false), [message,setMessage]=useState('');
 useEffect(()=>{let alive=true; setDraft(null);setStatus(null);setKey('');setMessage('');
 const load=()=>api<Settings>(`/spaces/${sid}/embedding`).then(s=>{if(alive){setStatus(s);setDraft(d=>d??s);}}).catch(e=>{if(alive)onError(e);});
 void load();const timer=setInterval(load,5000);return()=>{alive=false;clearInterval(timer);};},[sid]);
 async function save(clear=false){if(!draft)return;setBusy(true);try{
 const {enabled,base_url,model,dimensions,timeout_seconds,failure_threshold,cooldown_seconds,min_similarity}=draft;
 const data=await put<Settings>(`/spaces/${sid}/embedding`,{enabled,base_url,model,dimensions,timeout_seconds,failure_threshold,cooldown_seconds,min_similarity,...(clear?{api_key:''}:key?{api_key:key}:{})});setStatus(data);setDraft(data);setKey('');setMessage('已保存；后台自动补建索引。');
 }catch(e){onError(e);}finally{setBusy(false);}}
 async function test(){setBusy(true);try{const r=await post<{ok:boolean;reason?:string}>(`/spaces/${sid}/embedding/test`,{});setMessage(r.ok?'连接成功':`连接不可用：${routeStates[r.reason??'']??r.reason}；聊天仍使用程序召回。`);}catch(e){onError(e);}finally{setBusy(false);}}
 if(!draft||!status)return <section className="panel settings-section">加载语义检索状态…</section>;
 return <section className="panel settings-section">
 <div className="settings-section-heading"><div><h3>远程语义检索</h3><p>兼容 OpenAI Embeddings 接口。事件后台建索引，聊天不等待补建。</p></div></div>
 <p role="status">{!status.enabled?'未启用':status.circuit_open?`冷却中，约 ${status.retry_after_seconds} 秒后允许探测`:'已启用'} · 已索引 {status.indexed_chunks} 个片段 · 待建 {status.pending_chunks} 个片段</p>
 {status.last_error&&<p className="subtle-note">最近错误：{routeStates[status.last_error]??status.last_error}。查询失败会自动退回程序召回。</p>}
 {readOnly?<p>连接参数由 AstrBot 插件配置管理；请在那里修改并重载。</p>:<>
 <div className="setting-row"><label htmlFor="embedding-enabled">启用远程 embedding</label><button id="embedding-enabled" type="button" role="switch" aria-label="启用远程 embedding" aria-checked={draft.enabled} className={`switch ${draft.enabled ? "on" : ""}`} onClick={()=>setDraft({...draft,enabled:!draft.enabled})}><span /></button></div>
 <div className="embedding-fields" >
 <label className="field-label">服务地址<input aria-label="Embedding 服务地址" style={{width:'100%'}} placeholder="https://example.com/v1" value={draft.base_url} onChange={e=>setDraft({...draft,base_url:e.target.value})}/></label>
 <label className="field-label">模型名<input aria-label="Embedding 模型名" style={{width:'100%'}} value={draft.model} onChange={e=>setDraft({...draft,model:e.target.value})}/></label>
 <label className="field-label">API Key<input aria-label="Embedding API Key" style={{width:'100%'}} type="password" autoComplete="off" placeholder={status.has_key?'已配置；留空保留':'未配置'} value={key} onChange={e=>setKey(e.target.value)}/></label>
 <label className="field-label">单次超时（秒）<input aria-label="Embedding 超时" type="number" min="0.1" max="10" step="0.1" value={draft.timeout_seconds} onChange={e=>setDraft({...draft,timeout_seconds:Number(e.target.value)})}/></label>
 <label className="field-label">连续失败熔断次数<input aria-label="Embedding 熔断次数" type="number" min="1" max="10" value={draft.failure_threshold} onChange={e=>setDraft({...draft,failure_threshold:Number(e.target.value)})}/></label>
 <label className="field-label">冷却时间（秒）<input aria-label="Embedding 冷却时间" type="number" min="1" max="600" value={draft.cooldown_seconds} onChange={e=>setDraft({...draft,cooldown_seconds:Number(e.target.value)})}/></label>
 <label className="field-label">向量维度（可选）<input aria-label="Embedding 维度" type="number" min="1" max="16384" placeholder="由服务决定" value={draft.dimensions??''} onChange={e=>setDraft({...draft,dimensions:e.target.value?Number(e.target.value):null})}/></label>
 <label className="field-label">最低相似度<input aria-label="Embedding 最低相似度" type="number" min="0" max="1" step="0.05" value={draft.min_similarity} onChange={e=>setDraft({...draft,min_similarity:Number(e.target.value)})}/></label>
 </div>
 <p className="subtle-note">保存后在召回方式中选择「语义增强召回」。密钥仅保存在服务内存，重启后需重新填写，或设置 EMBEDDING_API_KEY。更换地址或模型会自动重建；期间继续程序召回。</p>
 <div className="retrieval-actions"><button className="button" disabled={busy||!status.enabled} onClick={test}>测试已保存连接</button><button className="button" disabled={busy||!status.has_key} onClick={()=>save(true)}>清除会话密钥</button><button className="button primary" disabled={busy} onClick={()=>save()}>保存连接</button></div>
 </>}
 {message&&<p role="status">{message}</p>}
 </section>;
}
