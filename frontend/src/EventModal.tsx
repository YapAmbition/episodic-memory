import { useEffect, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  Clock3,
  GitBranch,
  History,
  Loader2,
  PencilLine,
  Save,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { api, day, percent, post, put } from "./api";
import type { Anchors, MemoryEvent, Person } from "./types";

type Props = {
  sid: string;
  id: string;
  getTime: () => string;
  events: MemoryEvent[];
  people: Person[];
  onClose: () => void;
  onSaved: () => void;
  onError: (e: unknown) => void;
  onNavigate: (id: string) => void;
};
const anchorNames: Record<keyof Anchors, string> = {
  time: "时间锚点",
  scene: "场景与活动",
  details: "鲜明细节与原话",
  structure: "经历结构",
  triggers: "可能的触发描述",
};

export function EventModal({
  sid,
  id,
  getTime,
  events,
  people,
  onClose,
  onSaved,
  onError,
  onNavigate,
}: Props) {
  const [event, setEvent] = useState<MemoryEvent | null>(null),
    [editing, setEditing] = useState(id === "new"),
    [saving, setSaving] = useState(false);
  const [title, setTitle] = useState(""),
    [body, setBody] = useState(""),
    [salience, setSalience] = useState(0.5),
    [reason, setReason] = useState("");
  const [occurred, setOccurred] = useState(""),
    [personIds, setPersonIds] = useState<string[]>([]),
    [anchorText, setAnchorText] = useState<Record<string, string>>({}),
    [linkIds, setLinkIds] = useState<string[]>([]),
    [status, setStatus] = useState("closed");
  const [confirmDelete, setConfirmDelete] = useState(false),
    [revisions, setRevisions] = useState<
      { id: string; saved_at: string; event: MemoryEvent }[] | null
    >(null),
    [error, setError] = useState("");
  const base = `/spaces/${sid}/events`;
  useEffect(() => {
    setEditing(id === "new");
    setEvent(null);
    setError("");
    setRevisions(null);
    setConfirmDelete(false);
    if (id === "new") {
      setTitle("");
      setBody("");
      setSalience(0.5);
      setReason("");
      setOccurred("");
      setPersonIds(people[0] ? [people[0].id] : []);
      setAnchorText({});
      setLinkIds([]);
      setStatus("closed");
      return;
    }
    let cancelled = false;
    api<MemoryEvent>(`${base}/${id}?at=${encodeURIComponent(getTime())}`)
      .then((e) => {
        if (cancelled) return;
        setEvent(e);
        setTitle(e.title);
        setBody(e.body);
        setSalience(e.salience);
        setReason(e.salience_reason);
        setOccurred(e.occurred_at?.slice(0, 10) || "");
        setPersonIds(e.person_ids);
        setAnchorText(
          Object.fromEntries(
            Object.entries(e.anchors).map(([k, v]) => [k, v.join("\n")]),
          ),
        );
        setLinkIds(e.links.map((l) => l.target_id));
        setStatus(e.status);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [id, sid, getTime]);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", listener);
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", listener);
      document.body.style.overflow = before;
    };
  }, [onClose]);
  function beginEdit() {
    if (event) {
      setTitle(event.title);
      setBody(event.body);
      setSalience(event.salience);
      setReason(event.salience_reason);
      setOccurred(event.occurred_at?.slice(0, 10) || "");
      setPersonIds(event.person_ids);
      setStatus(event.status);
      setAnchorText(
        Object.fromEntries(
          Object.entries(event.anchors).map(([k, v]) => [k, v.join("\n")]),
        ),
      );
      setLinkIds(event.links.map((link) => link.target_id));
    }
    setEditing(true);
  }
  async function save() {
    setSaving(true);
    setError("");
    try {
      const occurredAt =
        occurred === (event?.occurred_at?.slice(0, 10) || "")
          ? event?.occurred_at || null
          : occurred
            ? new Date(occurred + "T12:00:00").toISOString()
            : null;
      const payload = {
        title,
        body,
        salience,
        salience_reason: reason,
        occurred_at: occurredAt,
        learned_at: event?.learned_at || getTime(),
        person_ids: personIds,
        anchors: Object.fromEntries(
          Object.keys(anchorNames).map((k) => [
            k,
            (anchorText[k] || "")
              .split("\n")
              .map((t) => t.trim())
              .filter(Boolean),
          ]),
        ),
        links: linkIds.map(
          (target_id) =>
            event?.links.find((l) => l.target_id === target_id) || {
              target_id,
              relation: "关联",
              strength: 0.8,
            },
        ),
        source_ids: event?.source_ids || [],
        status,
        ...(event ? { expected_version: event.version } : {}),
      };
      const saved = event
        ? await put<MemoryEvent>(`${base}/${id}`, payload)
        : await post<MemoryEvent>(base, payload);
      onSaved();
      setEditing(false);
      onNavigate(saved.id);
      const refreshed = await api<MemoryEvent>(
        `${base}/${saved.id}?at=${encodeURIComponent(getTime())}`,
      );
      setEvent(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }
  async function remove() {
    try {
      await api(`${base}/${id}`, { method: "DELETE" });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  }
  async function loadVersions() {
    try {
      setRevisions(await api(`${base}/${id}/versions`));
    } catch (e) {
      onError(e);
    }
  }
  const changePerson = (pid: string) =>
    setPersonIds((old) =>
      old.includes(pid) ? old.filter((p) => p !== pid) : [...old, pid],
    );
  const changeLink = (eid: string) =>
    setLinkIds((old) =>
      old.includes(eid) ? old.filter((p) => p !== eid) : [...old, eid],
    );
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        className="modal event-modal"
        role="dialog"
        aria-modal="true"
        aria-label={editing ? "编辑事件" : "事件详情"}
      >
        <div className="modal-header">
          <div>
            <span className="eyebrow">
              {id === "new" ? "A NEW EXPERIENCE" : "AN EVENT, IN FULL"}
            </span>
            <h2>
              {editing
                ? id === "new"
                  ? "记录一件事"
                  : "维护这段经历"
                : event?.title || "正在读取事件"}
            </h2>
          </div>
          <div className="modal-actions">
            {event && !editing && (
              <button className="button secondary compact" onClick={beginEdit}>
                <PencilLine size={14} />
                编辑
              </button>
            )}
            <button
              className="icon-button"
              aria-label="关闭事件详情"
              onClick={onClose}
            >
              <X size={21} />
            </button>
          </div>
        </div>
        {error && (
          <div role="alert" className="inline-error">
            {error}
          </div>
        )}
        {id !== "new" && !event && !error ? (
          <div className="loading-page">
            <Loader2 className="spin" />
            读取事件正文…
          </div>
        ) : editing ? (
          <form
            className="event-edit"
            onSubmit={(e) => {
              e.preventDefault();
              save();
            }}
          >
            <label className="field-label">
              事件标题
              <input
                autoFocus
                required
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={150}
                placeholder="给这件事一个有辨识度的名字"
              />
            </label>
            <div className="form-columns">
              <label className="field-label">
                发生日期<small>不确定时留空</small>
                <input
                  type="date"
                  value={occurred}
                  onChange={(e) => setOccurred(e.target.value)}
                />
              </label>
              <label className="field-label">
                事件阶段
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  <option value="closed">已发生</option>
                  <option value="open">仍有后续</option>
                  <option value="planned">尚在计划</option>
                  <option value="archived">已归档</option>
                </select>
              </label>
            </div>
            <label className="field-label">
              详细事件正文
              <small>
                保留经过、原话、小插曲与双方回应。可使用 [[事件ID]]
                引用已有事件。
              </small>
              <textarea
                required
                rows={9}
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
            </label>
            <div className="field-label">
              相关对话者
              <div className="check-chips">
                {people.map((p) => (
                  <label key={p.id}>
                    <input
                      type="checkbox"
                      checked={personIds.includes(p.id)}
                      onChange={() => changePerson(p.id)}
                    />
                    {p.name}
                  </label>
                ))}
              </div>
            </div>
            <label className="field-label">
              初始印象强度 <b>{percent(salience)}</b>
              <input
                type="range"
                min="0"
                max="1"
                step=".01"
                value={salience}
                onChange={(e) => setSalience(Number(e.target.value))}
              />
            </label>
            <label className="field-label">
              留下印象的依据
              <textarea
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="当时哪些信号说明这件事吸引了关注？"
              />
            </label>
            <div className="form-section-title">
              <Sparkles size={16} />
              检索锚点<small>每行一个，属于派生索引</small>
            </div>
            <div className="form-columns">
              {Object.entries(anchorNames).map(([key, label]) => (
                <label key={key} className="field-label">
                  {label}
                  <textarea
                    rows={3}
                    value={anchorText[key] || ""}
                    onChange={(e) =>
                      setAnchorText((old) => ({
                        ...old,
                        [key]: e.target.value,
                      }))
                    }
                  />
                </label>
              ))}
            </div>
            <div className="field-label">
              关联事件
              <div className="link-checklist">
                {events
                  .filter((e) => e.id !== id)
                  .map((e) => (
                    <label key={e.id}>
                      <input
                        type="checkbox"
                        checked={linkIds.includes(e.id)}
                        onChange={() => changeLink(e.id)}
                      />
                      <span>
                        {e.title}
                        <small>{e.id}</small>
                      </span>
                    </label>
                  ))}
                {events.length === 0 && (
                  <small>创建更多事件后，可以在这里建立关联。</small>
                )}
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="button secondary"
                onClick={() => (event ? setEditing(false) : onClose())}
              >
                取消
              </button>
              <button className="button primary" disabled={saving}>
                {saving ? (
                  <Loader2 size={15} className="spin" />
                ) : (
                  <Save size={15} />
                )}
                保存事件
              </button>
            </div>
          </form>
        ) : (
          event && (
            <div className="event-detail-body">
              <div className="event-meta">
                <span>
                  <Clock3 size={13} />
                  {day(event.occurred_at)}
                </span>
                <span>得知于 {day(event.learned_at)}</span>
                <code>{event.id}</code>
              </div>
              <div className="event-detail-layout">
                <article className="event-prose">
                  {event.body.split(/\n\n+/).map((p, i) => (
                    <p key={i}>{p}</p>
                  ))}
                </article>
                <aside className="memory-state-card">
                  <span className="eyebrow">MEMORY STATE</span>
                  <strong>{percent(event.memory.strength)}</strong>
                  <span>当前鲜明度</span>
                  <div className="mini-meter">
                    <i style={{ width: percent(event.memory.strength) }} />
                  </div>
                  <dl>
                    <div>
                      <dt>初始印象</dt>
                      <dd>{percent(event.salience)}</dd>
                    </div>
                    <div>
                      <dt>记忆形成至今</dt>
                      <dd>{Math.floor(event.memory.age_days)} 天</dd>
                    </div>
                    <div>
                      <dt>实际强化次数</dt>
                      <dd>{event.reinforcements.length} 次</dd>
                    </div>
                  </dl>
                </aside>
              </div>
              <div className="detail-section">
                <h3>
                  <Sparkles size={16} />
                  为什么留下印象
                </h3>
                <p>{event.salience_reason || "尚未记录明确依据。"}</p>
              </div>
              <div className="detail-section">
                <h3>
                  <TimerIcon />
                  从今天开始，记忆怎样淡去
                </h3>
                <div className="detail-curve">
                  <svg
                    viewBox="0 0 600 110"
                    role="img"
                    aria-label="未来一年记忆鲜明度变化"
                  >
                    <path
                      d="M0 100H600M0 55H600M0 10H600"
                      stroke="#eceae3"
                      fill="none"
                    />
                    {event.curve && (
                      <>
                        <path
                          d={
                            "M" +
                            event.curve
                              .map(
                                (p, i) =>
                                  `${(i * 600) / (event.curve!.length - 1)},${100 - p.strength * 90}`,
                              )
                              .join(" L")
                          }
                          fill="none"
                          stroke="#ba6a44"
                          strokeWidth="2.5"
                        />
                        {event.curve.map((p, i) => (
                          <circle
                            key={p.days}
                            cx={(i * 600) / (event.curve!.length - 1)}
                            cy={100 - p.strength * 90}
                            r="3.5"
                            fill="#ba6a44"
                          />
                        ))}
                      </>
                    )}
                  </svg>
                  <div className="curve-labels">
                    {event.curve?.map((p) => (
                      <span key={p.days}>
                        {p.days === 0 ? "今天" : `+${p.days}天`}
                      </span>
                    ))}
                  </div>
                </div>
                <small className="muted">
                  按当前参数计算，假设之后没有新的真实重提。横轴为分段时间节点，分数是工程状态。
                </small>
              </div>
              <div className="detail-section">
                <h3>
                  <BookOpen size={16} />
                  这件事的入口
                </h3>
                <div className="anchor-detail-grid">
                  {Object.entries(event.anchors)
                    .filter(([, v]) => v.length)
                    .map(([kind, values]) => (
                      <div key={kind}>
                        <span>{anchorNames[kind as keyof Anchors]}</span>
                        <div className="simple-chips">
                          {values.map((v, i) => (
                            <span key={i}>{v}</span>
                          ))}
                        </div>
                      </div>
                    ))}
                </div>
              </div>
              {(event.links.length > 0 || event.backlinks?.length !== 0) && (
                <div className="detail-section">
                  <h3>
                    <GitBranch size={16} />
                    关联的事情
                  </h3>
                  {event.links.map((l) => (
                    <button
                      className="linked-event"
                      key={l.target_id}
                      onClick={() => onNavigate(l.target_id)}
                    >
                      <span>
                        {events.find((e) => e.id === l.target_id)?.title ||
                          l.target_id}
                        <small>
                          {l.relation} · 连接强度 {percent(l.strength)}
                        </small>
                      </span>
                      <ArrowRight size={15} />
                    </button>
                  ))}
                  {event.backlinks
                    ?.filter(
                      (b) => !event.links.some((l) => l.target_id === b.id),
                    )
                    .map((b) => (
                      <button
                        className="linked-event"
                        key={b.id}
                        onClick={() => onNavigate(b.id)}
                      >
                        <span>
                          {b.title}
                          <small>此事件引用了当前事件</small>
                        </span>
                        <ArrowRight size={15} />
                      </button>
                    ))}
                </div>
              )}
              <details className="source-details">
                <summary>
                  原始来源 · {event.sources?.length || 0} 条消息
                  <ChevronDown size={14} />
                </summary>
                {event.sources?.map((s) => (
                  <div key={s.id}>
                    <small>
                      {s.role === "user" ? "用户" : "助手"} · {day(s.at)}
                    </small>
                    <p>{s.content}</p>
                    <code>{s.id}</code>
                  </div>
                ))}
              </details>
              <div className="detail-bottom">
                <button className="button text compact" onClick={loadVersions}>
                  <History size={14} />
                  查看历史版本
                </button>
                <button
                  className="button text danger compact"
                  onClick={() => setConfirmDelete((v) => !v)}
                >
                  <Trash2 size={14} />
                  删除事件
                </button>
              </div>
              {revisions && (
                <div className="revision-list">
                  {revisions.length === 0 ? (
                    <p>暂无历史版本。</p>
                  ) : (
                    revisions.map((r) => (
                      <details key={r.id}>
                        <summary>
                          {new Date(r.saved_at).toLocaleString("zh-CN")} ·{" "}
                          {r.event.title}
                        </summary>
                        <pre>{r.event.body}</pre>
                      </details>
                    ))
                  )}
                </div>
              )}
              {confirmDelete && (
                <div className="delete-confirm">
                  <p>
                    删除此事件、历史版本和包含其正文的召回记录。原始聊天消息会保留，其他事件中的引用会移除。
                  </p>
                  <button className="button danger-button" onClick={remove}>
                    确认删除
                  </button>
                </div>
              )}
            </div>
          )
        )}
      </section>
    </div>
  );
}

function TimerIcon() {
  return <Clock3 size={16} />;
}
