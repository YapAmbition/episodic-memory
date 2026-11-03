import { useEffect, useState } from "react";
import { BookOpen, History, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { api, post, put } from "./api";
import type { Config, Conversation, Message, MemoryEvent, Person, Recall, Space, Stats } from "./types";
import "./plugin.css";

export function PluginConfiguration({config, space}: {config: Config; space?: Space}) {
  return <section className="plugin-console">
    <div className="page-heading"><div><div className="eyebrow">ASTRBOT · CONFIGURATION</div><h1>一处配置，共同生效。</h1><p>模型、关注对象和整理计划由 AstrBot 插件配置管理。</p></div><ShieldCheck size={30}/></div>
    <div className="plugin-notice">请到 AstrBot → 插件 → 记忆切片 → 配置修改，保存后重载插件。这里展示当前空间实际使用的参数；事件正文可以在「事件记忆」中编辑。</div>
    <div className="plugin-summary">
      <article><small>模型来源</small><strong>AstrBot 已配置的聊天模型</strong></article>
      <article><small>召回方式</small><strong>{config.default_retrieval_mode === "hybrid" ? "语义增强召回" : "程序多路召回"}</strong></article>
      <article><small>自动整理</small><strong>{config.schedule_enabled ? `每 ${config.schedule_interval_minutes} 分钟` : "已暂停"}</strong></article>
      <article><small>基础半衰期</small><strong>{config.half_life_days} 天</strong></article>
      <article><small>每轮新增注入字符上限</small><strong>{(space?.host?.injection_policy?.context_char_budget ?? config.context_char_budget).toLocaleString()}</strong></article>
      <article><small>每轮普通召回事件上限</small><strong>{space?.host?.injection_policy?.recall_limit ?? config.max_events} 件</strong></article>
      <article><small>片段目标 / 硬上限</small><strong>{config.fragment_target_chars} / {config.fragment_max_chars} 字符</strong></article>
      <article><small>关联展开深度</small><strong>{config.max_depth} 层</strong></article>
    </div>
    <details className="plugin-config-details"><summary>查看完整生效配置</summary><pre>{JSON.stringify(config,null,2)}</pre></details>
  </section>;
}

export function PluginConsole({space, stats, onMaintain, onJobs, busy}: {
  space: Space; stats: Stats | null; onMaintain: () => void; onJobs: () => void; busy: boolean;
}) {
  const [conversations,setConversations]=useState<Conversation[]>([]);
  const [cid,setCid]=useState("");
  const [messages,setMessages]=useState<Message[]>([]);
  const [people,setPeople]=useState<Person[]>([]);
  const [error,setError]=useState("");
  const [updated,setUpdated]=useState("");
  const [query,setQuery]=useState("");
  const [result,setResult]=useState<Recall|null>(null);
  const [searching,setSearching]=useState(false);
  const base=`/spaces/${space.id}`;
  const filterByFocus=space.host?.injection_policy?.filter_by_focus ?? false;
  useEffect(()=>{setResult(null);setQuery("");},[base]);
  useEffect(()=>{
    let alive=true, running=false;
    const load=async()=>{
      if (running) return;
      running=true;
      try {
        const [c,p]=await Promise.all([api<Conversation[]>(`${base}/conversations`),api<Person[]>(`${base}/persons`)]);
        const selected=cid && c.some(x=>x.id===cid) ? cid : c[0]?.id || "";
        const m=selected ? await api<Message[]>(`${base}/conversations/${selected}/messages`) : [];
        if(alive){setConversations(c);setPeople(p);setCid(selected);setMessages(m);setError("");setUpdated(new Date().toLocaleTimeString());}
      } catch(e){if(alive)setError(e instanceof Error?e.message:"读取消息失败");}
      finally{running=false;}
    };
    void load(); const timer=setInterval(()=>{if(!document.hidden)void load();},5000);
    return ()=>{alive=false;clearInterval(timer);};
  },[base,cid]);
  async function search(){
    if(!query.trim() || searching)return;
    setSearching(true);setError("");setResult(null);
    try{setResult(await post<Recall>(`${base}/context`,{query, retrieval_mode:"local", filter_by_focus:filterByFocus, trace:true}));}
    catch(e){setError(e instanceof Error?e.message:"检索失败");}
    finally{setSearching(false);}
  }
  return <section className="plugin-console">
    <div className="page-heading"><div><div className="eyebrow">ASTRBOT · LIVE MEMORY</div><h1>聊天里的小事，在这里留下。</h1><p>{space.host?.label || space.name} · {space.host?.active ? "与插件实时共用记忆" : "历史空间，已停用整理"}</p></div>
      <div className="plugin-actions"><button className="button secondary" onClick={onJobs}><History size={16}/>整理记录</button><button className="button primary" disabled={busy || !space.host?.active} onClick={onMaintain}><RefreshCw size={16}/>整理记忆</button></div>
    </div>
    <div className="plugin-summary"><article><small>已记录的事情</small><strong>{stats?.events ?? "—"}<em> 件</em></strong></article><article><small>待整理消息</small><strong>{stats?.pending_messages ?? "—"}<em> 条</em></strong></article><article><small>数据来源</small><strong>插件原始会话</strong><small>每 5 秒刷新 · {updated || "正在连接"}</small></article></div>
    {error && <div role="alert" className="plugin-notice">{error}</div>}
    <div className="plugin-columns">
      <article className="plugin-card"><header><div><BookOpen size={18}/><h2>真实对话</h2></div><select aria-label="查看会话" value={cid} onChange={e=>{setMessages([]);setCid(e.target.value);}}>{conversations.map(c=><option key={c.id} value={c.id}>{c.title}</option>)}</select></header>
        <div className="plugin-messages">{messages.length ? messages.map(m=><div key={m.id} className={`plugin-message ${m.role}`}><div className="plugin-message-meta"><strong>{m.role === "assistant" ? "AstrBot" : people.find(p=>p.id===m.person_id)?.name || "未知发送者"}</strong><time>{new Date(m.at).toLocaleString()}</time></div><p>{m.content}</p>{m.retention === "buffer" && <small>临时上下文 · 相关性待整理判断</small>}</div>) : <p className="subtle-note">还没有记录到消息。在 AstrBot 中聊天后，这里会自动更新。</p>}</div>
        <footer>只展示原始会话；请在聊天平台继续对话。</footer>
      </article>
      <article className="plugin-card"><header><div><Search size={18}/><h2>找一件记得的事</h2></div></header><form className="plugin-search" onSubmit={e=>{e.preventDefault();void search();}}><textarea aria-label="检索记忆" value={query} onChange={e=>setQuery(e.target.value)} placeholder="输入一句话或一个细节…" maxLength={20000}/><button className="button secondary" disabled={searching || !query.trim()}>{searching?"正在检索…":"程序检索"}</button></form><p className="subtle-note">检索范围：{filterByFocus ? "仅当前关注人物" : "当前空间全部事件"}。仅使用程序检索，不调用 embedding，不会写入聊天消息或强化记忆。</p>{result && <div className="plugin-result"><small>{result.selected.length} 件相关事件</small><pre>{result.context || "这次没有找到超过唤起门槛的记忆。"}</pre></div>}</article>
    </div>
  </section>;
}


type PinState = {event_ids: string[]; characters: number; limit: number; errors: string[]};
export function PinnedMemorySettings({sid, active}: {sid: string; active: boolean}) {
  const [events,setEvents]=useState<MemoryEvent[]>([]);
  const [selected,setSelected]=useState<string[]>([]);
  const [saved,setSaved]=useState<PinState|null>(null);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [busy,setBusy]=useState(true);
  const [query,setQuery]=useState("");
  const [saveFailed,setSaveFailed]=useState(false);
  useEffect(()=>{
    let alive=true;
    Promise.all([api<MemoryEvent[]>(`/spaces/${sid}/events`),api<PinState>(`/spaces/${sid}/pinned-memories`)])
      .then(([rows,pins])=>{if(alive){setEvents(rows);setSaved(pins);setSelected(pins.event_ids);}})
      .catch(e=>{if(alive)setError(String(e));})
      .finally(()=>{if(alive)setBusy(false);});
    return ()=>{alive=false;};
  },[sid]);
  async function save(){
    setBusy(true);setError("");setNotice("");
    try{const pins=await put<PinState>(`/spaces/${sid}/pinned-memories`,selected);setSaved(pins);setSaveFailed(false);setNotice("常驻选择已保存，下次模型请求生效。");}
    catch(e){setSaveFailed(true);setError(e instanceof Error && e.message.startsWith("暂时没保存成功") ? e.message : `未能确认保存成功，你的选择已保留，可稍后重试。${e instanceof Error ? e.message : "请检查服务连接。"}`);}finally{setBusy(false);}
  }
  const missing=selected.filter(id=>!events.some(e=>e.id===id));
  return <section className="plugin-card">
    <header><h2>常驻记忆</h2><span>{selected.length} / 3 件</span></header>
    <div className="plugin-search">
      <p>当前群聊或私聊空间独立保存。每轮完整注入最新正文，即使历史上下文中已有这件事。普通召回使用剩余字符预算。</p>
      {saved && <p>已保存选择占用 {saved.characters.toLocaleString()} / {saved.limit.toLocaleString()} 字符（含说明和标签）。保存时严格校验，超限请减少选择、缩短事件或提高插件上限。</p>}
      {!!saved?.errors.length && <div role="alert" className="plugin-notice">{saved.errors.join("；")}</div>}
      <input aria-label="搜索可常驻事件" placeholder="按标题或事件 ID 查找" value={query} onChange={e=>setQuery(e.target.value)}/>
      <div className="plugin-pin-list">
        {missing.map(id=><label key={id}><input type="checkbox" checked onChange={()=>setSelected(selected.filter(x=>x!==id))} disabled={busy || !active}/>已删除：{id}（取消选择后保存）</label>)}
        {events.filter(e=>selected.includes(e.id) || (e.status!=="archived" && (e.title.includes(query) || e.id.includes(query)))).map(e=><label key={e.id}>
          <input type="checkbox" checked={selected.includes(e.id)} disabled={busy || !active || (!selected.includes(e.id) && selected.length>=3)} onChange={()=>{setNotice("");setSelected(selected.includes(e.id)?selected.filter(id=>id!==e.id):[...selected,e.id]);}}/>
          <span><strong>{e.title}</strong><small>{e.id}{e.frozen ? " · 已冻结" : ""} · 正文 {e.body.length.toLocaleString()} 字符{e.status==="archived" ? " · 已归档，请取消选择" : ""}</small></span>
        </label>)}
        {!busy && !events.length && <p>还没有事件，先整理聊天记录再选择。</p>}
      </div>
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      <button className="button primary" disabled={busy || !active || !saved} onClick={()=>void save()}>{busy ? "处理中…" : saveFailed ? "重试保存" : "保存常驻选择"}</button>
      <small>若事件后续增长、删除或归档导致选择失效，将暂停本轮记忆注入；通过 /记忆切片 查看原因并在这里调整。总长度不会突破上限。</small>
    </div>
  </section>;
}
