import { useEffect, useState } from "react";
import { api, post, put } from "./api";
import type { Config, Person } from "./types";

export function FocusSettings({sid, config, onConfig, notify, onError}: {
  sid: string; config: Config; onConfig: (c: Config) => void;
  notify: (s: string) => void; onError: (e: unknown) => void;
}) {
  const [people, setPeople] = useState<Person[]>([]);
  const [focus, setFocus] = useState(config.focus_person_id || "");
  const [hours, setHours] = useState(config.focus_buffer_hours || 24);
  const [limit, setLimit] = useState(config.focus_buffer_limit || 2000);
  const [person, setPerson] = useState<Person | null>(null);
  const [aliasText, setAliasText] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let cancelled = false;
    api<Person[]>(`/spaces/${sid}/persons`).then(p => {
      if (cancelled) return;
      setPeople(p); setFocus(config.focus_person_id || "");
      setPerson(p.find(x => x.id === config.focus_person_id) || null);
      setAliasText((p.find(x => x.id === config.focus_person_id)?.aliases || []).join(","));
    }).catch(onError);
    return () => { cancelled = true; };
  }, [sid, config.focus_person_id]);
  async function save() {
    setSaving(true);
    try {
      let id = focus;
      if (person) {
        const body = {name: person.name, relation: person.relation, platform: person.platform || "", external_uid: person.external_uid || "", aliases: aliasText.split(/[,，]/).map(s=>s.trim()).filter(Boolean)};
        const saved = person.id === "new"
          ? await post<Person>(`/spaces/${sid}/persons`, body)
          : await put<Person>(`/spaces/${sid}/persons/${person.id}`, body);
        id = saved.id;
        setPeople(old => [...old.filter(p => p.id !== saved.id), saved]);
        setPerson(saved); setFocus(id);
      }
      onConfig(await put<Config>(`/spaces/${sid}/config`, {...config, focus_person_id: id || null, focus_buffer_hours: hours, focus_buffer_limit: limit}));
      notify("关注设置已保存，新消息按此范围整理");
    } catch (e) { onError(e); } finally { setSaving(false); }
  }
  return <section className="panel" style={{padding: 26, maxWidth: 860}}>
    <h2>记住与谁有关的事情</h2>
    <p className="maintenance-note">每个空间最多关注一个人。未配置时，保留通用对话整理，人物不参与召回或整理候选加分。</p>
    <label className="field-label">关注对象<select value={focus} onChange={e => {
      setFocus(e.target.value);
      setAliasText((people.find(p => p.id === e.target.value)?.aliases || []).join(","));
      setPerson(e.target.value === "new" ? {id:"new", name:"", relation:"关注对象", aliases:[]} : people.find(p => p.id === e.target.value) || null);
    }}>
      <option value="">不配置 · 通用模式</option>
      {people.map(p => <option key={p.id} value={p.id}>{p.name}{p.external_uid ? ` · ${p.platform}/${p.external_uid}` : ` · ${p.id}`}</option>)}
      <option value="new">新建关注对象</option>
    </select></label>
    {person && <>
      <div className="form-columns">
        <label className="field-label">名字<input value={person.name} maxLength={60} onChange={e => setPerson({...person, name:e.target.value})}/></label>
        <label className="field-label">别名（用逗号分隔）<input value={aliasText} onChange={e => setAliasText(e.target.value)}/></label>
        <label className="field-label">平台<input placeholder="例如 wechat、discord" value={person.platform || ""} onChange={e => setPerson({...person, platform:e.target.value})}/></label>
        <label className="field-label">平台 UID<input value={person.external_uid || ""} onChange={e => setPerson({...person, external_uid:e.target.value})}/></label>
      </div>
      <p className="maintenance-note">平台和 UID 一起填写。同名人物不会自动合并；仅凭有歧义的名字不认定为关注对象。接入方应使用人物 ID 标记发送者、回复和 @。</p>
      <div className="form-columns">
        <label className="field-label">临时缓冲保留小时数<input type="number" min={1} max={168} value={hours} onChange={e => setHours(Number(e.target.value))}/></label>
        <label className="field-label">临时消息上限<input type="number" min={200} max={20000} value={limit} onChange={e => setLimit(Number(e.target.value))}/></label>
      </div>
      <p className="maintenance-note">新消息暂存，相关事件的来源长期保留，其他临时原文到期清理；原有历史消息不自动删除。缓冲满时接入方需稍后重试。关注模式需要整理模型判断关联；未配置模型时暂存候选，不自动归档为事件。</p>
    </>}
    <button className="button primary" disabled={saving || (!!person && !person.name.trim())} onClick={save}>{saving ? "保存中…" : "保存关注设置"}</button>
  </section>;
}
