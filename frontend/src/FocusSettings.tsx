import { useEffect, useRef, useState } from "react";
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
  const [saveError, setSaveError] = useState("");
  const [invalidField, setInvalidField] = useState("");
  const nameInput = useRef<HTMLInputElement>(null);
  const platformInput = useRef<HTMLInputElement>(null);
  const uidInput = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setSaveError(""); setInvalidField(""); setLoading(true);
    api<Person[]>(`/spaces/${sid}/persons`).then(p => {
      if (cancelled) return;
      setPeople(p); setFocus(config.focus_person_id || "");
      setPerson(p.find(x => x.id === config.focus_person_id) || null);
      setAliasText((p.find(x => x.id === config.focus_person_id)?.aliases || []).join(","));
    }).catch(onError).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [sid, config.focus_person_id]);
  async function save() {
    setSaveError(""); setInvalidField("");
    const name = person?.name.trim() || "";
    const platform = person?.platform?.trim() || "";
    const uid = person?.external_uid?.trim() || "";
    if (person && (!name || (!!platform !== !!uid))) {
      const field = !name ? "name" : !platform ? "platform" : "uid";
      setInvalidField(field);
      setSaveError(!name ? "请填写关注对象的名字。" : !platform ? "请填写平台：平台和平台 UID 必须一起填写。" : "请填写平台 UID：平台和平台 UID 必须一起填写。");
      ({name: nameInput, platform: platformInput, uid: uidInput}[field]).current?.focus();
      return;
    }
    setSaving(true);
    try {
      let id = focus;
      if (person) {
        const body = {name, relation: person.relation, platform, external_uid: uid, aliases: aliasText.split(/[,，]/).map(s=>s.trim()).filter(Boolean)};
        const saved = person.id === "new"
          ? await post<Person>(`/spaces/${sid}/persons`, body)
          : await put<Person>(`/spaces/${sid}/persons/${person.id}`, body);
        id = saved.id;
        setPeople(old => [...old.filter(p => p.id !== saved.id), saved]);
        setPerson(saved); setFocus(id);
      }
      onConfig(await put<Config>(`/spaces/${sid}/config`, {...config, focus_person_id: id || null, focus_buffer_hours: hours, focus_buffer_limit: limit}));
      notify("关注设置已保存，新消息按此范围整理");
    } catch (e) { setSaveError(e instanceof Error ? e.message : "保存失败，请稍后重试。"); onError(e); } finally { setSaving(false); }
  }
  return <section className="panel" style={{padding: 26, maxWidth: 860}}>
    <h2>记住与谁有关的事情</h2>
    <p className="maintenance-note">每个空间最多关注一个人。未配置时，保留通用对话整理，人物不参与召回或整理候选加分。</p>
    <label className="field-label">关注对象<select disabled={loading || saving} value={focus} onChange={e => {
      setSaveError(""); setInvalidField("");
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
        <label className="field-label">名字<input ref={nameInput} aria-invalid={invalidField === "name"} aria-describedby={saveError ? "focus-save-error" : undefined} value={person.name} maxLength={60} onChange={e => setPerson({...person, name:e.target.value})}/></label>
        <label className="field-label">别名（用逗号分隔）<input value={aliasText} onChange={e => setAliasText(e.target.value)}/></label>
        <label className="field-label">平台<input ref={platformInput} maxLength={80} aria-invalid={invalidField === "platform"} aria-describedby={saveError ? "focus-save-error" : undefined} placeholder="例如 wechat、discord" value={person.platform || ""} onChange={e => setPerson({...person, platform:e.target.value})}/></label>
        <label className="field-label">平台 UID<input ref={uidInput} maxLength={200} aria-invalid={invalidField === "uid"} aria-describedby={saveError ? "focus-save-error" : undefined} value={person.external_uid || ""} onChange={e => setPerson({...person, external_uid:e.target.value})}/></label>
      </div>
      <p className="maintenance-note">平台和 UID 必须一起填写，也可同时留空用于普通对话。同名人物不会自动合并；仅凭有歧义的名字不认定为关注对象。接入方应使用人物 ID 标记发送者、回复和 @。</p>
      <div className="form-columns">
        <label className="field-label">临时缓冲保留小时数<input type="number" min={1} max={168} value={hours} onChange={e => setHours(Number(e.target.value))}/></label>
        <label className="field-label">临时消息上限<input type="number" min={200} max={20000} value={limit} onChange={e => setLimit(Number(e.target.value))}/></label>
      </div>
      <p className="maintenance-note">新消息暂存，相关事件的来源长期保留，其他临时原文到期清理；原有历史消息不自动删除。缓冲满时接入方需稍后重试。关注模式需要整理模型判断关联；未配置模型时暂存候选，不自动归档为事件。</p>
    </>}
    {saveError && <p id="focus-save-error" role="alert" style={{color: "#a64132", background: "#fff1eb", border: "1px solid #ebc6b8", borderRadius: 8, padding: "10px 12px", fontSize: 13, lineHeight: 1.6}}>{saveError}</p>}
    <button className="button primary" disabled={saving || loading} onClick={save}>{saving ? "保存中…" : "保存关注设置"}</button>
  </section>;
}
