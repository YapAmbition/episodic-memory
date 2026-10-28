import { MaintenanceDetails } from "./MaintenanceDetails";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  AudioLines,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Code2,
  Copy,
  Database,
  ExternalLink,
  FileText,
  FlaskConical,
  GitBranch,
  History,
  Layers3,
  Loader2,
  MessageCircle,
  MoreHorizontal,
  Network,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Sparkles,
  Timer,
  Trash2,
  UserRound,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  api,
  post,
  put,
  downloadMemory,
  percent,
  day,
} from "./api";
import type {
  Config,
  Conversation,
  Job,
  MemoryEvent,
  Message,
  ModelConfig,
  Person,
  Recall,
  Space,
  Stats,
  RetrievalMode,
} from "./types";
import { EventModal } from "./EventModal";
import { ExperimentTimeControl } from "./ExperimentTimeControl";
import type { ExperimentClock } from "./ExperimentTimeControl";
import { SettingsPage } from "./SettingsPage";
import { MonitoringPage } from "./MonitoringPage";
import {
  latency,
  modeName,
  retrievalModes,
  routeNames,
  routeStates,
  stageNames,
} from "./retrievalUi";
import "./retrieval.css";

type Page = "lab" | "events" | "graph" | "settings" | "monitoring" | "api";
const pages = [
  { id: "lab", label: "对话实验室", icon: MessageCircle },
  { id: "events", label: "事件记忆", icon: BookOpen },
  { id: "graph", label: "关联网络", icon: Network },
  { id: "settings", label: "记忆设置", icon: Settings2 },
  { id: "monitoring", label: "运行监控", icon: Activity },
  { id: "api", label: "API 接入", icon: Code2 },
] as const;
const statusLabel: Record<string, string> = {
  active: "鲜明",
  quiet: "渐淡",
  dormant: "沉睡",
  future: "尚未得知",
};
const jobLabel: Record<string, string> = {
  deferred: "材料不足，部分暂缓",
  queued: "排队中",
  running: "正在整理",
  completed: "已完成",
  failed: "失败",
  skipped: "已跳过",
};

function Logo() {
  return (
    <svg viewBox="0 0 40 40" aria-hidden="true">
      <path
        d="M20 3.5 34.3 11.8v16.4L20 36.5 5.7 28.2V11.8Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="m5.7 11.8 14.3 8.3 14.3-8.3M20 20.1v16.4M20 3.5v16.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <circle cx="20" cy="20" r="4.4" fill="currentColor" />
    </svg>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(() =>
    window.location.hash === "#monitoring" ? "monitoring" : "lab",
  );
  const [spaces, setSpaces] = useState<Space[]>([]),
    [sid, setSid] = useState("");
  const [people, setPeople] = useState<Person[]>([]),
    [pid, setPid] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]),
    [cid, setCid] = useState("");
  const [replyId, setReplyId] = useState("");
  const [mentionId, setMentionId] = useState("");
  const isGroup = conversations.find(c => c.id === cid)?.kind === "group";
  const [messages, setMessages] = useState<Message[]>([]),
    [events, setEvents] = useState<MemoryEvent[]>([]);
  const [stats, setStats] = useState<Stats | null>(null),
    [config, setConfig] = useState<Config | null>(null),
    [model, setModel] = useState<ModelConfig | null>(null);
  const [clock, setClock] = useState<ExperimentClock>(() => ({
    mode: "realtime",
    at: new Date().toISOString(),
  }));
  const getTime = useCallback(
    () => clock.mode === "realtime" ? new Date().toISOString() : clock.at,
    [clock],
  );
  const [query, setQuery] = useState(""),
    [draft, setDraft] = useState("");
  const [trace, setTrace] = useState<Recall | null>(null),
    [traceTab, setTraceTab] = useState("memories");
  const [busy, setBusy] = useState(false),
    [recalling, setRecalling] = useState(false),
    [useMemory, setUseMemory] = useState(true);
  const [toast, setToast] = useState(""),
    [error, setError] = useState(""),
    [booting, setBooting] = useState(true),
    [serviceConnected, setServiceConnected] = useState(false);
  const [eventModal, setEventModal] = useState<string | null>(null),
    [filter, setFilter] = useState("all"),
    [eventQuery, setEventQuery] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]),
    [showJobs, setShowJobs] = useState(false);
  const [auth, setAuth] = useState(false),
    [token, setToken] = useState(""),
    [spaceModal, setSpaceModal] = useState(false),
    [spaceName, setSpaceName] = useState("");
  const [personModal, setPersonModal] = useState(false),
    [personName, setPersonName] = useState("");
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode | "default">(
    "default",
  );
  const bottom = useRef<HTMLDivElement>(null),
    toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    recallSeq = useRef(0),
    recallController = useRef<AbortController | null>(null),
    pendingSend = useRef<{ content: string; cid: string; id: string; identity: string } | null>(
      null,
    ),
    currentScope = useRef(sid),
    currentClock = useRef(clock);
  currentScope.current = sid;
  currentClock.current = clock;
  const notify = useCallback((text: string) => {
    setToast(text);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 3500);
  }, []);
  const fail = useCallback(
    (e: unknown) =>
      setError(e instanceof Error ? e.message : "请求失败，请重试"),
    [],
  );
  const base = `/spaces/${sid}`;

  const bootstrap = useCallback(async () => {
    try {
      const s = await api<Space[]>("/spaces");
      setServiceConnected(true);
      setSpaces(s);
      if (s.length)
        setSid((old) => (s.some((x) => x.id === old) ? old : s[0].id));
    } catch (e) {
      setServiceConnected(false);
      fail(e);
    } finally {
      setBooting(false);
    }
  }, [fail]);
  useEffect(() => {
    if (sid && config) api<Person[]>(`${base}/persons`).then(setPeople).catch(fail);
  }, [sid, config]);
  useEffect(() => {
    bootstrap();
    const handler = () => setAuth(true);
    window.addEventListener("memory-slice-auth", handler);
    return () => window.removeEventListener("memory-slice-auth", handler);
  }, [bootstrap]);
  const refresh = useCallback(async () => {
    if (!sid) return;
    const at = getTime();
    const [e, s, j] = await Promise.all([
      api<MemoryEvent[]>(`${base}/events?at=${encodeURIComponent(at)}`),
      api<Stats>(`${base}/stats?at=${encodeURIComponent(at)}`),
      api<Job[]>(`${base}/jobs`),
    ]);
    if (currentScope.current !== sid || currentClock.current !== clock) return;
    setServiceConnected(true);
    setEvents(e);
    setStats(s);
    setJobs(j);
  }, [sid, base, clock, getTime]);
  function switchSpace(value: string) {
    setPid("");
    setCid("");
    setQuery("");
    setTrace(null);
    setRetrievalMode("default");
    setEvents([]);
    setStats(null);
    setEventModal(null);
    pendingSend.current = null;
    recallSeq.current += 1;
    setSid(value);
  }
  useEffect(() => {
    if (!sid) return;
    let cancelled = false;
    setTrace(null);
    setMessages([]);
    setReplyId(""); setMentionId("");
    setConfig(null);
    setError("");
    (async () => {
      try {
        const [p, c, cfg, m] = await Promise.all([
          api<Person[]>(`${base}/persons`),
          api<Conversation[]>(`${base}/conversations`),
          api<Config>(`${base}/config`),
          api<ModelConfig>(`${base}/model`),
        ]);
        if (cancelled) return;
        setPeople(p);
        setConversations(c);
        setConfig(cfg);
        setModel(m);
        setPid(p[0]?.id || "");
        setCid(c.find((x) => x.person_id === p[0]?.id)?.id || "");
        await refresh();
      } catch (e) {
        if (!cancelled) fail(e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sid]); // space scope changes reset all UI state
  useEffect(() => {
    refresh().catch(fail);
  }, [refresh, fail]);
  useEffect(() => {
    setReplyId(""); setMentionId("");
    if (!cid || !sid) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    api<Message[]>(`${base}/conversations/${cid}/messages`)
      .then((m) => {
        if (!cancelled) {
          setMessages(m);
          setQuery(
            [...m].reverse().find((x) => x.role === "user")?.content || "",
          );
          const latest = [...m].reverse().find((message) => message.recall_id);
          if (latest?.recall_id)
            api<Recall>(`${base}/recalls/${latest.recall_id}`)
              .then((value) => {
                if (!cancelled) setTrace(value);
              })
              .catch(() => {
                if (!cancelled) setTrace(null);
              });
          else setTrace(null);
        }
      })
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, [cid, sid]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages, busy]);
  const doRecall = useCallback(
    async (text: string) => {
      if (!sid || !pid || busy || !text.trim()) return;
      recallController.current?.abort();
      const controller = new AbortController();
      recallController.current = controller;
      const seq = ++recallSeq.current;
      setRecalling(true);
      setTrace(null);
      setError("");
      try {
        const r = await post<Recall>(
          `${base}/context`,
          {
            person_id: pid,
            reply_to_person_id: messages.find(m => m.id === replyId)?.person_id || null,
            mention_ids: mentionId ? [mentionId] : [],
            query: text,
            at: getTime(),
            recent_messages: messages
              .slice(-4)
              .map((message) => ({
                role: message.role,
                content: message.content,
              })),
            ...(retrievalMode === "default"
              ? {}
              : { retrieval_mode: retrievalMode }),
            trace: true,
          },
          { signal: controller.signal },
        );
        if (seq === recallSeq.current) setTrace(r);
      } catch (e) {
        if (seq === recallSeq.current && !controller.signal.aborted) fail(e);
      } finally {
        if (seq === recallSeq.current) setRecalling(false);
      }
    },
    [sid, pid, base, getTime, retrievalMode, messages, busy, fail, replyId, mentionId],
  );
  useEffect(() => {
    recallController.current?.abort();
    recallSeq.current += 1;
    setRecalling(false);
    return () => recallController.current?.abort();
  }, [sid, pid, cid, clock, retrievalMode, page]);
  useEffect(() => {
    if (!jobs.some((j) => ["queued", "running"].includes(j.status))) return;
    const interval = setInterval(() => refresh().catch(fail), 1200);
    return () => clearInterval(interval);
  }, [jobs, refresh, fail]);

  async function startConversation(personId = pid, kind: "direct" | "group" = isGroup ? "group" : "direct") {
    if (!personId) return;
    pendingSend.current = null;
    try {
      const c = await post<Conversation>(`${base}/conversations`, {
        person_id: personId,
        title: kind === "group" ? "群聊试验" : "新的对话",
        kind,
      });
      setConversations((old) => [c, ...old]);
      setCid(c.id);
      setMessages([]);
      setTrace(null);
      setQuery("");
      setDraft("");
      notify("新对话已创建");
    } catch (e) {
      fail(e);
    }
  }
  function changePerson(personId: string) {
    setPid(personId);
    if (isGroup) return;
    setTrace(null);
    setQuery("");
    const c = conversations.find((x) => x.person_id === personId);
    if (c) setCid(c.id);
    else {
      setCid("");
      startConversation(personId);
    }
  }
  async function send(text = draft) {
    if (!text.trim() || busy) return;
    setBusy(true);
    recallController.current?.abort();
    recallSeq.current += 1;
    setRecalling(false);
    setDraft("");
    setError("");
    let current = cid;
    try {
      if (!current) {
        const c = await post<Conversation>(`${base}/conversations`, {
          person_id: pid,
          title: text.slice(0, 25),
        });
        current = c.id;
        setConversations((old) => [c, ...old]);
        setCid(current);
      }
      if (
        !pendingSend.current ||
        pendingSend.current.content !== text ||
        pendingSend.current.cid !== current ||
        pendingSend.current.identity !== JSON.stringify([pid, replyId, mentionId])
      )
        pendingSend.current = {
          content: text,
          cid: current,
          id: crypto.randomUUID(),
          identity: JSON.stringify([pid, replyId, mentionId]),
        };
      const result = await post<{
        user: Message;
        assistant: Message;
        recall: Recall | null;
      }>(`${base}/conversations/${current}/chat`, {
        content: text,
        at: getTime(),
        sender_id: pid,
        in_reply_to: replyId || null,
        mention_ids: mentionId ? [mentionId] : [],
        use_memory: useMemory,
        client_id: pendingSend.current.id,
        ...(retrievalMode === "default"
          ? {}
          : { retrieval_mode: retrievalMode }),
        trace: true,
      });
      if (currentScope.current !== sid) return;
      pendingSend.current = null;
      setReplyId(""); setMentionId("");
      setMessages(
        await api<Message[]>(`${base}/conversations/${current}/messages`),
      );
      setTrace(result.recall);
      if (query !== text) {
        setQuery(text);
      }
      await refresh();
    } catch (e) {
      setDraft(text);
      fail(e);
      if (current)
        setMessages(
          await api<Message[]>(
            `${base}/conversations/${current}/messages`,
          ).catch(() => []),
        );
    } finally {
      setBusy(false);
    }
  }
  async function maintain() {
    try {
      await post(`${base}/maintenance`, {
        conversation_id: cid || null,
        at: getTime(),
      });
      setShowJobs(true);
      await refresh();
      notify("整理任务已提交");
    } catch (e) {
      fail(e);
    }
  }
  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      notify("已复制");
    } catch {
      notify("当前浏览器无法自动复制，请手动选择文本");
    }
  }
  async function addSpace() {
    try {
      const s = await post<Space>("/spaces", {
        name: spaceName,
        description: "",
      });
      setSpaces((old) => [...old, s]);
      switchSpace(s.id);
      setSpaceModal(false);
      setSpaceName("");
      notify("记忆空间已创建");
    } catch (e) {
      fail(e);
    }
  }
  async function addPerson() {
    try {
      const p = await post<Person>(`${base}/persons`, {
        name: personName,
        relation: "日常对话",
      });
      setPeople((old) => [...old, p]);
      setPersonModal(false);
      setPersonName("");
      changePerson(p.id);
      notify("对话者已添加");
    } catch (e) {
      fail(e);
    }
  }
  const filtered = events.filter(
    (e) =>
      (filter === "all" || e.memory_status === filter) &&
      (!eventQuery ||
        `${e.title}${e.body}${JSON.stringify(e.anchors)}`.includes(eventQuery)),
  );
  const currentPerson = people.find((p) => p.id === pid);
  const currentPage = pages.find((p) => p.id === page)!;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("lab");
            window.history.replaceState(
              null,
              "",
              window.location.pathname + window.location.search,
            );
          }}
        >
          <span className="brand-mark">
            <Logo />
          </span>
          <span>
            记忆切片<small>MEMORY SLICE</small>
          </span>
        </a>
        <div className="sidebar-label">工作空间</div>
        <div className="space-picker">
          <Layers3 size={17} />
          <select
            aria-label="记忆空间"
            value={sid}
            onChange={(e) => switchSpace(e.target.value)}
          >
            {spaces.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <button aria-label="新建记忆空间" onClick={() => setSpaceModal(true)}>
            <Plus size={15} />
          </button>
        </div>
        <nav>
          {pages.map((p) => (
            <button
              key={p.id}
              className={`nav-item ${page === p.id ? "active" : ""}`}
              aria-current={page === p.id ? "page" : undefined}
              onClick={() => {
                setPage(p.id);
                window.history.replaceState(
                  null,
                  "",
                  window.location.pathname +
                    window.location.search +
                    (p.id === "monitoring" ? "#monitoring" : ""),
                );
                setError("");
              }}
            >
              <p.icon size={18} />
              <span>{p.label}</span>
              {p.id === "events" && <small>{stats?.events ?? "—"}</small>}
              {p.id === "lab" && page === "lab" && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <Sparkles size={18} />
          <p>
            事情会淡去，
            <br />
            共同经历仍有回响。
          </p>
          <span>EPISODIC MEMORY</span>
        </div>
        <div className="sidebar-bottom">
          <span
            className={`service-dot ${serviceConnected ? "" : "offline"}`}
          />
          <span>{serviceConnected ? "记忆服务已连接" : "等待服务连接"}</span>
          <small>v0.6.0</small>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumbs">
            {page === "monitoring" ? "服务" : "工作空间"}
            <ChevronRight size={13} />
            {page === "monitoring"
              ? "全部记忆空间"
              : spaces.find((s) => s.id === sid)?.name || "加载中"}
            <ChevronRight size={13} />
            <strong>{currentPage.label}</strong>
          </div>
          <div className="top-actions">
            <span className="mode-label">
              <span />
              {page === "monitoring"
                ? "全服务指标"
                : model?.mode === "llm"
                  ? "模型对话模式"
                  : "本地演示模式"}
            </span>
            <button
              className="button secondary compact"
              onClick={() =>
                downloadMemory(sid)
                  .then(() => notify("事件正文和配置已导出"))
                  .catch(fail)
              }
              disabled={!sid}
            >
              <ArrowDownToLine size={15} />
              导出记忆
            </button>
            <span className="user-avatar">M</span>
          </div>
        </header>
        {error && (
          <div role="alert" className="error-banner">
            {error}
            <button aria-label="关闭错误" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        {booting ? (
          <div className="loading-page">
            <Loader2 className="spin" />
            正在连接记忆服务…
          </div>
        ) : page === "monitoring" ? (
          <div className="page-content">
            <MonitoringPage onConnectionChange={setServiceConnected} />
          </div>
        ) : !sid ? (
          <div className="empty-state">
            <Database size={35} />
            <h2>建立第一个记忆空间</h2>
            <p>独立保存一段共同历史。</p>
            <button
              className="button primary"
              onClick={() => setSpaceModal(true)}
            >
              创建空间
            </button>
          </div>
        ) : (
          <div className="page-content">
            {page === "lab" && (
              <>
                <section className="page-heading">
                  <div>
                    <div className="eyebrow">
                      <span />
                      THE MEMORY PLAYGROUND
                    </div>
                    <h1>
                      让对话，带上共同经历<span>。</span>
                    </h1>
                    <p>换一个时间、一个人，看看哪些事情会浮现。</p>
                  </div>
                  <button
                    className="button secondary"
                    onClick={maintain}
                    disabled={jobs.some((j) =>
                      ["queued", "running"].includes(j.status),
                    )}
                  >
                    <RefreshCw
                      size={16}
                      className={
                        jobs.some((j) => j.status === "running") ? "spin" : ""
                      }
                    />
                    整理记忆
                    {(stats?.pending_messages ?? 0) > 0 && (
                      <span className="count-badge">
                        {stats?.pending_messages}
                      </span>
                    )}
                  </button>
                </section>
                <div className="stat-grid">
                  {[
                    {
                      label: "已记录的事情",
                      value: stats?.events,
                      icon: BookOpen,
                      detail: "完整保存每一段经历",
                      tone: "orange",
                    },
                    {
                      label: "仍然鲜明",
                      value: stats?.active,
                      icon: Sparkles,
                      detail: "随情境自然浮现",
                      tone: "green",
                    },
                    {
                      label: "暂时沉睡",
                      value: stats?.dormant,
                      icon: Timer,
                      detail: "等待一个合适的线索",
                      tone: "blue",
                    },
                    {
                      label: "事件间的连接",
                      value: stats?.links,
                      icon: GitBranch,
                      detail: "把独立片段连成故事",
                      tone: "purple",
                    },
                  ].map((item) => (
                    <div className="stat-card" key={item.label}>
                      <div className="stat-info">
                        <span>{item.label}</span>
                        <strong>
                          {item.value === undefined
                            ? "—"
                            : String(item.value).padStart(2, "0")}
                        </strong>
                        <small>{item.detail}</small>
                      </div>
                      <span className={`stat-icon ${item.tone}`}>
                        <item.icon size={20} />
                      </span>
                    </div>
                  ))}
                </div>
                <div className="experiment-toolbar">
                  <div className="toolbar-group">
                    <UserRound size={15} />
                    <span>对话者</span>
                    <select
                      aria-label="选择对话者"
                      disabled={busy}
                      value={pid}
                      onChange={(e) => changePerson(e.target.value)}
                    >
                      {people.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="icon-button"
                      aria-label="添加对话者"
                      onClick={() => setPersonModal(true)}
                    >
                      <Plus size={14} />
                    </button>
                  </div>
                  <button className="button secondary compact" disabled={busy} onClick={() => startConversation(pid, "group")}>新建群聊试验</button>
                  <div className="toolbar-divider" />
                  <ExperimentTimeControl
                    clock={clock}
                    disabled={busy || recalling}
                    onChange={setClock}
                  />
                  <label className="memory-toggle">
                    <button
                      role="switch"
                      aria-checked={useMemory}
                      className={`switch ${useMemory ? "on" : ""}`}
                      onClick={() => setUseMemory((v) => !v)}
                    >
                      <span />
                    </button>
                    注入记忆
                  </label>
                </div>
                {isGroup && <div className="lab-retrieval-control" style={{marginBottom: 12}}>
                  <span>群聊 · 上方对话者即本轮发送者</span>
                  <label>@ 提及 <select aria-label="提及人物" value={mentionId} onChange={e => setMentionId(e.target.value)}><option value="">无</option>{people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
                  {replyId && <span>回复：{messages.find(m=>m.id===replyId)?.content.slice(0,35)} <button onClick={()=>setReplyId("")}>取消回复</button></span>}
                </div>}
                <div
                  className="lab-retrieval-control"
                  style={{ margin: "-4px 0 16px" }}
                >
                  <Settings2 size={14} />
                  <span>本轮召回</span>
                  <select
                    aria-label="本轮召回方式"
                    value={retrievalMode}
                    disabled={busy || recalling}
                    onChange={(event) =>
                      setRetrievalMode(
                        event.target.value as RetrievalMode | "default",
                      )
                    }
                  >
                    <option value="default">
                      跟随空间 ·{" "}
                      {modeName(config?.default_retrieval_mode ?? "local")}
                    </option>
                    {retrievalModes.map((mode) => (
                      <option key={mode.id} value={mode.id}>
                        {mode.name}
                      </option>
                    ))}
                  </select>
                  <span>
                    {(retrievalMode === "default"
                      ? config?.default_retrieval_mode
                      : retrievalMode) === "llm"
                      ? "将调用生成式模型"
                      : "历史会话只读取已有结果；点击重新唤起才会检索"}
                  </span>
                </div>
                <div className="lab-grid">
                  <section className="panel chat-panel">
                    <div className="panel-header">
                      <div className="panel-heading">
                        <span className="small-avatar">
                          <AudioLines size={19} />
                        </span>
                        <div>
                          <h3>对话实验室</h3>
                          <small>
                            {model?.mode === "llm"
                              ? model.model
                              : "本地规则回放"}
                            <span className="tiny-dot" />与
                            {currentPerson?.name || "我"}的共同历史
                          </small>
                        </div>
                      </div>
                      <button
                        className="button text compact"
                        onClick={() => startConversation()}
                        disabled={busy}
                      >
                        <Plus size={14} />
                        新对话
                      </button>
                    </div>
                    <div className="chat-scroll">
                      <div className="chat-date">
                        <span /> {day(getTime())} · 记忆实验 <span />
                      </div>
                      {messages.length === 0 && (
                        <div className="chat-empty">
                          <div className="empty-orbit">
                            <Logo />
                          </div>
                          <h3>从一句日常开始</h3>
                          <p>聊聊今天的小事，或给旧记忆一个线索。</p>
                        </div>
                      )}
                      {messages.map((m) => (
                        <div className={`message ${m.role}`} key={m.id}>
                          <div
                            className={`message-avatar ${m.role === "assistant" ? "agent" : ""}`}
                          >
                            {m.role === "user" ? (
                              <UserRound size={15} />
                            ) : (
                              <Logo />
                            )}
                          </div>
                          <div className="message-content">
                            <div className="message-label">
                              {m.role === "user"
                                ? people.find(p => p.id === m.person_id)?.name || currentPerson?.name
                                : "记忆伙伴"}
                              {m.example && <span>预置示例</span>}
                              {m.response_mode === "local" && (
                                <span>规则回放</span>
                              )}
                            </div>
                            <div className="message-bubble">{m.content}</div>
                            {isGroup && <button className="time-step" onClick={()=>setReplyId(m.id)}>回复这条</button>}
                            <div className="message-time">
                              {new Date(m.at).toLocaleTimeString("zh-CN", {
                                hour: "2-digit",
                                minute: "2-digit",
                              })}
                              {m.recall_id && (
                                <button
                                  onClick={() =>
                                    api<Recall>(
                                      `${base}/recalls/${m.recall_id}`,
                                    )
                                      .then(setTrace)
                                      .catch(fail)
                                  }
                                >
                                  <Sparkles size={11} />
                                  查看本轮记忆
                                </button>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                      {busy && (
                        <div className="thinking">
                          <span />
                          <span />
                          <span />
                          正在连接对话与记忆
                        </div>
                      )}
                      <div ref={bottom} />
                    </div>
                    <div className="chat-composer">
                      <div className="prompt-chips">
                        {[
                          "下次生日想在家过",
                          "折腾半天发现插座没开",
                          "今天又下雨了",
                        ].map((p) => (
                          <button
                            key={p}
                            onClick={() => setDraft(p)}
                            disabled={busy}
                          >
                            {p}
                            <ArrowUp size={11} />
                          </button>
                        ))}
                      </div>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          send();
                        }}
                      >
                        <textarea
                          aria-label="对话消息"
                          placeholder="说点什么，给记忆一个线索…"
                          value={draft}
                          onChange={(e) => setDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (
                              e.key === "Enter" &&
                              !e.shiftKey &&
                              !e.nativeEvent.isComposing
                            ) {
                              e.preventDefault();
                              send();
                            }
                          }}
                          rows={2}
                        />
                        <button
                          className="send-button"
                          aria-label="发送消息"
                          disabled={busy || !draft.trim()}
                        >
                          {busy ? (
                            <Loader2 size={18} className="spin" />
                          ) : (
                            <ArrowUp size={20} />
                          )}
                        </button>
                      </form>
                      <div className="composer-hint">
                        <span>
                          <kbd>↵</kbd> 发送 · Shift + Enter 换行
                        </span>
                        <span>
                          <span className="tiny-dot" />
                          {useMemory ? "记忆上下文已开启" : "本轮不注入记忆"}
                        </span>
                      </div>
                    </div>
                  </section>
                  <section className="panel recall-panel">
                    <div className="panel-header">
                      <div>
                        <h3>
                          <Sparkles size={17} />
                          此刻浮现的记忆
                        </h3>
                        <p>看见一句话背后的共同历史</p>
                      </div>
                      <button
                        className="icon-button"
                        aria-label="重新唤起记忆"
                        disabled={recalling || busy}
                        onClick={() =>
                          doRecall(
                            query ||
                              [...messages]
                                .reverse()
                                .find((m) => m.role === "user")?.content ||
                              draft,
                          )
                        }
                      >
                        <RefreshCw
                          size={15}
                          className={recalling ? "spin" : ""}
                        />
                      </button>
                    </div>
                    <div className="tabs recall-tabs">
                      {[
                        {
                          id: "memories",
                          label: "唤起事件",
                          count: trace?.selected.length,
                        },
                        { id: "context", label: "注入上下文" },
                        { id: "candidates", label: "候选" },
                      ].map((t) => (
                        <button
                          key={t.id}
                          className={traceTab === t.id ? "active" : ""}
                          onClick={() => setTraceTab(t.id)}
                        >
                          {t.label}
                          {t.count !== undefined && <span>{t.count}</span>}
                        </button>
                      ))}
                    </div>
                    {trace?.route_status && (
                      <details
                        className="recall-diagnostics"
                        open={trace.degraded || undefined}
                      >
                        <summary>
                          <span>
                            {modeName(trace.effective_mode)}
                            {trace.degraded ? " · 已降级" : ""}
                          </span>
                          <span>{latency(trace.elapsed_ms)}</span>
                        </summary>
                        {trace.warnings?.map((warning) => (
                          <div className="retrieval-warning" key={warning.code}>
                            {warning.message}
                          </div>
                        ))}
                        <div className="recall-route-list">
                          {Object.entries(trace.route_status).map(
                            ([name, status]) => (
                              <div key={name}>
                                <span>
                                  {routeNames[name] ?? name}{" "}
                                  <small>
                                    {routeStates[status.status] ??
                                      status.status}
                                  </small>
                                </span>
                                <span>
                                  {status.candidate_count} 件
                                  {status.elapsed_ms != null
                                    ? ` · ${latency(status.elapsed_ms)}`
                                    : ""}
                                </span>
                              </div>
                            ),
                          )}
                        </div>
                        <div className="recall-stage-list">
                          {Object.entries(trace.timings_ms ?? {})
                            .filter(([stage]) => stage !== "total")
                            .map(([stage, ms]) => (
                              <span key={stage}>
                                {stageNames[stage] ?? stage} {latency(ms)}
                              </span>
                            ))}
                        </div>
                      </details>
                    )}
                    <div className="recall-scroll">
                      {recalling && (
                        <div className="recall-loading">
                          <Loader2 size={15} className="spin" />
                          正在寻找联想入口…
                        </div>
                      )}
                      {!trace ? (
                        <div className="empty-state compact-empty">
                          <Layers3 size={30} />
                          <h3>记忆等待被唤起</h3>
                          <p>发送一句话，或点击右上角重新检索。</p>
                        </div>
                      ) : traceTab === "memories" ? (
                        <>
                          {trace.selected.length > 0 ? (
                            <>
                              <div className="recall-summary">
                                <span className="tiny-dot" />
                                {trace.seeds.length} 个入口 ·{" "}
                                {trace.selected.length} 件事浮现
                                <span>
                                  {trace.effective_mode
                                    ? modeName(trace.effective_mode)
                                    : trace.strategy === "hybrid_llm"
                                      ? "模型联想"
                                      : "本地锚点"}
                                </span>
                              </div>
                              {trace.selected.map((item, i) => (
                                <article
                                  className="recall-card"
                                  key={item.event_id}
                                >
                                  <div className="recall-card-top">
                                    <span className="event-number">
                                      {String(i + 1).padStart(2, "0")}
                                    </span>
                                    <span
                                      className={`state ${item.event.memory_status}`}
                                    >
                                      {statusLabel[item.event.memory_status]}
                                    </span>
                                    <small>
                                      {item.depth === 0
                                        ? "直接唤起"
                                        : `关联展开 · ${item.depth} 层`}
                                    </small>
                                  </div>
                                  <h4>
                                    <button
                                      onClick={() =>
                                        setEventModal(item.event_id)
                                      }
                                    >
                                      {item.event.title}
                                      <ChevronRight size={15} />
                                    </button>
                                  </h4>
                                  <p className="event-excerpt">
                                    {item.event.body.slice(0, 145)}
                                    {item.event.body.length > 145 ? "…" : ""}
                                  </p>
                                  <div className="anchor-chips">
                                    {item.reasons.slice(0, 2).map((r, i) => (
                                      <span key={i}>
                                        <span /> {r}
                                      </span>
                                    ))}
                                  </div>
                                  <div className="strength-line">
                                    <span>
                                      唤起强度{" "}
                                      <b title="工程激活分，不是概率">
                                        {percent(item.score)}
                                      </b>
                                    </span>
                                    <div>
                                      <i
                                        style={{
                                          width: percent(
                                            Math.min(1, item.score),
                                          ),
                                        }}
                                      />
                                    </div>
                                  </div>
                                  <div className="recall-card-footer">
                                    <span>
                                      <Clock3 size={11} />
                                      {day(item.event.occurred_at)}
                                    </span>
                                    <button
                                      onClick={() =>
                                        setEventModal(item.event_id)
                                      }
                                    >
                                      查看完整事件
                                      <ArrowRight size={12} />
                                    </button>
                                  </div>
                                </article>
                              ))}
                            </>
                          ) : (
                            <div className="empty-state compact-empty">
                              <Timer size={30} />
                              <h3>这轮没有记忆浮现</h3>
                              <p>
                                没有足够强的线索，或者记忆已经淡出。可以换个线索再次尝试。
                              </p>
                            </div>
                          )}
                          {trace.omitted.length > 0 && (
                            <div className="subtle-note">
                              {trace.omitted.length}{" "}
                              件事超出上下文预算，正文未被截断。
                            </div>
                          )}
                        </>
                      ) : traceTab === "context" ? (
                        <>
                          <div className="code-toolbar">
                            <span>
                              {trace.context_chars.toLocaleString()} 字符 ·
                              完整事件正文
                            </span>
                            <button onClick={() => copy(trace.context)}>
                              <Copy size={13} />
                              复制
                            </button>
                          </div>
                          <pre className="context-code">
                            {trace.context || "本轮没有注入记忆。"}
                          </pre>
                        </>
                      ) : (
                        <div className="candidate-list">
                          {trace.candidates.map((c) => (
                            <button
                              key={c.event_id}
                              className="candidate"
                              onClick={() => setEventModal(c.event_id)}
                            >
                              <div>
                                <strong>{c.title}</strong>
                                <small>
                                  {c.reasons[0] || "没有明确的当前线索"}
                                </small>
                                <div className="candidate-route-tags">
                                  {c.retrieval_routes?.map((route) => (
                                    <span key={route}>
                                      {routeNames[route] ?? route}
                                    </span>
                                  ))}
                                </div>
                              </div>
                              <span className={c.eligible ? "eligible" : ""}>
                                {percent(c.score)}
                              </span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="recall-footer">
                      <span>
                        <Database size={12} />
                        {trace?.effective_mode
                          ? modeName(trace.effective_mode)
                          : "多路检索 · 可解释"}
                      </span>
                      <span>
                        {trace
                          ? `${latency(trace.elapsed_ms)} · ${trace.context_chars} 字符`
                          : "等待检索"}
                      </span>
                    </div>
                  </section>
                </div>
                <p className="bottom-note">
                  <span className="tiny-dot" />
                  示例空间包含虚构经历。时间推进仅改变实验视角，不会改写记忆正文或强化历史。
                </p>
              </>
            )}
            {page === "events" && (
              <>
                <section className="page-heading">
                  <div>
                    <div className="eyebrow">
                      <span />
                      THE EVENT ARCHIVE
                    </div>
                    <h1>
                      那些发生过的事情<span>。</span>
                    </h1>
                    <p>保留经过，也保留让这件事独一无二的小细节。</p>
                  </div>
                  <button
                    className="button primary"
                    onClick={() => setEventModal("new")}
                  >
                    <Plus size={16} />
                    记录一件事
                  </button>
                </section>
                <div className="archive-toolbar">
                  <div className="filter-tabs">
                    {[
                      ["all", "全部事件"],
                      ["active", "仍然鲜明"],
                      ["quiet", "正在淡去"],
                      ["dormant", "暂时沉睡"],
                    ].map(([id, label]) => (
                      <button
                        key={id}
                        className={filter === id ? "active" : ""}
                        onClick={() => setFilter(id)}
                      >
                        {label}
                        {id === "all" && <span>{events.length}</span>}
                      </button>
                    ))}
                  </div>
                  <label className="search-box">
                    <Search size={16} />
                    <input
                      placeholder="搜索经过、原话或锚点…"
                      value={eventQuery}
                      onChange={(e) => setEventQuery(e.target.value)}
                    />
                  </label>
                </div>
                <div className="event-grid">
                  {filtered.map((e, i) => (
                    <button
                      className="event-card"
                      key={e.id}
                      onClick={() => setEventModal(e.id)}
                    >
                      <div className="event-card-top">
                        <span className={`event-card-icon tone-${i % 4}`}>
                          <FileText size={20} />
                        </span>
                        <span className={`state ${e.memory_status}`}>
                          {statusLabel[e.memory_status]}
                        </span>
                      </div>
                      <span className="event-date">
                        {day(e.occurred_at)} ·{" "}
                        {e.origin === "demo" ? "示例事件" : "事件记录"}
                      </span>
                      <h3>{e.title}</h3>
                      <p>
                        {e.body.slice(0, 115)}
                        {e.body.length > 115 ? "…" : ""}
                      </p>
                      <div className="simple-chips">
                        {[...e.anchors.details, ...e.anchors.time]
                          .slice(0, 3)
                          .map((a, j) => (
                            <span key={j}>{a}</span>
                          ))}
                      </div>
                      <div className="event-card-bottom">
                        <span>
                          <GitBranch size={13} />
                          {e.links.length} 个关联
                        </span>
                        <span>
                          当前鲜明度 <b>{percent(e.memory.strength)}</b>
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
                {filtered.length === 0 && (
                  <div className="empty-state">
                    <BookOpen size={32} />
                    <h3>这里还没有事情</h3>
                    <p>可以新建事件，或从一段对话开始整理。</p>
                  </div>
                )}
              </>
            )}
            {page === "graph" && (
              <GraphPage events={events} onOpen={setEventModal} />
            )}
            {page === "settings" && config && model && (
              <SettingsPage
                sid={sid}
                config={config}
                model={model}
                onConfig={(c) => {
                  setConfig(c);
                  refresh().catch(fail);
                }}
                onModel={setModel}
                notify={notify}
                onError={fail}
              />
            )}
            {page === "api" && (
              <ApiPage sid={sid} pid={pid} cid={cid} copy={copy} />
            )}
          </div>
        )}
      </main>
      {toast && (
        <div className="toast" role="status">
          <Check size={16} />
          {toast}
        </div>
      )}
      {eventModal && (
        <EventModal
          sid={sid}
          id={eventModal}
          getTime={getTime}
          events={events}
          people={people}
          onClose={() => setEventModal(null)}
          onSaved={() => {
            refresh().catch(fail);
            notify("事件已更新");
          }}
          onError={fail}
          onNavigate={setEventModal}
        />
      )}
      {showJobs && (
        <div className="modal-backdrop" onClick={() => setShowJobs(false)}>
          <section
            className="modal jobs-modal"
            role="dialog"
            aria-modal="true"
            aria-label="整理任务"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <span className="eyebrow">MAINTENANCE</span>
                <h2>记忆整理记录</h2>
              </div>
              <button
                className="icon-button"
                aria-label="关闭"
                onClick={() => setShowJobs(false)}
              >
                <X size={20} />
              </button>
            </div>
            <p className="modal-description">
              记录事件整理任务；后台读取不会强化记忆。
            </p>
            {jobs.length === 0 ? (
              <p className="subtle-note">暂无整理记录。</p>
            ) : (
              jobs.map((j) => (
                <div className="job-row" key={j.id}>
                  <span className={`job-dot ${j.status}`} />
                  <div>
                    <strong>
                      {jobLabel[j.status]}
                    </strong>
                    <p>
                      {j.error ||
                        (j.method === "local_archive"
                            ? "本地原文归档 · 未进行模型事件理解"
                            : j.method === "llm"
                              ? "模型事件整理"
                              : j.status === "completed"
                                ? "没有待整理的新消息"
                                : "正在检查和整理对话")}
                    </p>
                    <MaintenanceDetails job={j} onRetry={async (id) => { try { await post(`${base}/jobs/${id}/retry`, {}); await refresh(); notify("剩余消息已重新提交整理"); } catch (e) { fail(e); } }} />
                    <small>
                      {new Date(j.created_at).toLocaleString("zh-CN")}
                    </small>
                  </div>
                  <span>
                    {`${j.message_count} 条消息`}
                    <br />
                    {j.event_ids.length} 件事情
                  </span>
                </div>
              ))
            )}
          </section>
        </div>
      )}
      {(spaceModal || personModal || auth) && (
        <div className="modal-backdrop">
          <section
            className="modal small-modal"
            role="dialog"
            aria-modal="true"
            aria-label={
              auth ? "服务认证" : spaceModal ? "新建空间" : "添加对话者"
            }
          >
            <div className="modal-header">
              <h2>
                {auth
                  ? "连接记忆服务"
                  : spaceModal
                    ? "创建记忆空间"
                    : "添加对话者"}
              </h2>
              {!auth && (
                <button
                  className="icon-button"
                  onClick={() => {
                    setSpaceModal(false);
                    setPersonModal(false);
                  }}
                  aria-label="关闭"
                >
                  <X size={20} />
                </button>
              )}
            </div>
            <p className="modal-description">
              {auth
                ? "当前服务需要访问令牌。令牌仅保存在此浏览器。"
                : spaceModal
                  ? "为不同 agent 或不同生活语境保存独立的记忆。"
                  : "人物身份将作为记忆唤起的一个线索。"}
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (auth) {
                  localStorage.setItem("memory-slice-token", token);
                  setAuth(false);
                  bootstrap();
                } else if (spaceModal) addSpace();
                else addPerson();
              }}
            >
              <label className="field-label">
                {auth ? "服务访问令牌" : spaceModal ? "空间名称" : "称呼"}
                <input
                  autoFocus
                  type={auth ? "password" : "text"}
                  required
                  value={auth ? token : spaceModal ? spaceName : personName}
                  onChange={(e) =>
                    auth
                      ? setToken(e.target.value)
                      : spaceModal
                        ? setSpaceName(e.target.value)
                        : setPersonName(e.target.value)
                  }
                  placeholder={
                    auth
                      ? "输入 MEMORY_API_TOKEN"
                      : spaceModal
                        ? "例如：日常生活"
                        : "例如：小林"
                  }
                />
              </label>
              <button className="button primary">
                {auth ? "连接服务" : "创建"}
                <ArrowRight size={15} />
              </button>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}

function GraphPage({
  events,
  onOpen,
}: {
  events: MemoryEvent[];
  onOpen: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1),
    [selected, setSelected] = useState("");
  const width = 900,
    height = 540,
    cx = 450,
    cy = 270;
  const points = events.map((e, i) => {
    const angle = (i / Math.max(events.length, 1)) * Math.PI * 2 - Math.PI / 2;
    return {
      ...e,
      x: cx + Math.cos(angle) * 235,
      y: cy + Math.sin(angle) * 190,
    };
  });
  const lookup = Object.fromEntries(points.map((p) => [p.id, p]));
  const current = events.find((e) => e.id === selected);
  return (
    <>
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <span />A NETWORK OF EXPERIENCES
          </div>
          <h1>
            一件事，想起另一件事<span>。</span>
          </h1>
          <p>每条连接都有来由。点击节点，展开一段完整经历。</p>
        </div>
        <div className="graph-legend">
          <i className="active" />
          鲜明
          <i className="quiet" />
          渐淡
          <i className="dormant" />
          沉睡
        </div>
      </section>
      <div className="graph-layout">
        <section className="panel graph-panel">
          <div className="panel-header">
            <h3>
              <Network size={16} />
              事件关联网络
            </h3>
            <span className="muted">
              {events.length} 个事件 ·{" "}
              {events.reduce((n, e) => n + e.links.length, 0)} 条连接
            </span>
          </div>
          <svg
            className="memory-graph"
            viewBox={`0 0 ${width} ${height}`}
            role="group"
            aria-label="可交互的事件关联网络"
          >
            <defs>
              <pattern
                id="grid"
                width="22"
                height="22"
                patternUnits="userSpaceOnUse"
              >
                <circle cx="1" cy="1" r=".8" fill="#e3e1da" />
              </pattern>
            </defs>
            <rect width={width} height={height} fill="url(#grid)" />
            <g
              transform={`translate(${cx * (1 - zoom)},${cy * (1 - zoom)}) scale(${zoom})`}
            >
              {points.flatMap((p) =>
                p.links.map((link, i) => {
                  const t = lookup[link.target_id];
                  return t ? (
                    <g key={p.id + i}>
                      <path
                        d={`M${p.x},${p.y} Q${cx},${cy} ${t.x},${t.y}`}
                        fill="none"
                        stroke={
                          selected === p.id || selected === t.id
                            ? "#c77b56"
                            : "#b7b9ae"
                        }
                        strokeWidth="1.6"
                        strokeDasharray="4 5"
                      />
                      <text
                        x={(p.x + t.x + cx) / 3}
                        y={(p.y + t.y + cy) / 3 - 5}
                        textAnchor="middle"
                        className="edge-label"
                      >
                        {link.relation}
                      </text>
                    </g>
                  ) : null;
                }),
              )}
              {points.map((p, i) => (
                <g
                  key={p.id}
                  className={`graph-node ${p.memory_status}`}
                  role="button"
                  tabIndex={0}
                  aria-label={p.title}
                  onClick={() => setSelected(p.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") setSelected(p.id);
                  }}
                >
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={selected === p.id ? 38 : 34}
                    fill="white"
                    stroke={selected === p.id ? "#b85e38" : "#e5e3dc"}
                    strokeWidth={selected === p.id ? 2 : 1}
                  />
                  <circle cx={p.x} cy={p.y} r="26" className="node-fill" />
                  <text
                    x={p.x}
                    y={p.y + 5}
                    textAnchor="middle"
                    className="node-number"
                  >
                    {String(i + 1).padStart(2, "0")}
                  </text>
                  <text
                    x={p.x}
                    y={p.y + 57}
                    textAnchor="middle"
                    className="node-title"
                  >
                    {p.title.length > 15 ? p.title.slice(0, 15) + "…" : p.title}
                  </text>
                  <text
                    x={p.x}
                    y={p.y + 75}
                    textAnchor="middle"
                    className="node-detail"
                  >
                    {statusLabel[p.memory_status]} ·{" "}
                    {percent(p.memory.strength)}
                  </text>
                </g>
              ))}
            </g>
          </svg>
          <div className="graph-controls">
            <button
              className="icon-button"
              aria-label="缩小"
              onClick={() => setZoom((z) => Math.max(0.5, z - 0.1))}
            >
              <ZoomOut size={17} />
            </button>
            <button onClick={() => setZoom(1)}>
              {Math.round(zoom * 100)}%
            </button>
            <button
              className="icon-button"
              aria-label="放大"
              onClick={() => setZoom((z) => Math.min(1.8, z + 0.1))}
            >
              <ZoomIn size={17} />
            </button>
          </div>
        </section>
        <aside className="panel graph-detail">
          <span className="eyebrow">EVENT DETAILS</span>
          {current ? (
            <>
              <span className={`state ${current.memory_status}`}>
                {statusLabel[current.memory_status]}
              </span>
              <h2>{current.title}</h2>
              <p>{current.body.slice(0, 400)}</p>
              <div className="simple-chips">
                {current.anchors.details.map((d) => (
                  <span key={d}>{d}</span>
                ))}
              </div>
              <button
                className="button secondary"
                onClick={() => onOpen(current.id)}
              >
                查看完整事件
                <ArrowRight size={15} />
              </button>
            </>
          ) : (
            <>
              <Network size={38} className="muted" />
              <h3>沿着一条线索</h3>
              <p>选择一个节点，查看事情的经过、当时留下的印象和关联。</p>
              <div className="graph-note">
                没有连线的事情仍然保留。只有有依据的联系，才会出现在这里。
              </div>
            </>
          )}
        </aside>
      </div>
    </>
  );
}

function ApiPage({
  sid,
  pid,
  cid,
  copy,
}: {
  sid: string;
  pid: string;
  cid: string;
  copy: (s: string) => void;
}) {
  const [tab, setTab] = useState("context");
  const origin = window.location.origin;
  const samples: Record<
    string,
    { method: string; path: string; body: unknown; description: string }
  > = {
    context: {
      method: "POST",
      path: `/spaces/${sid}/context`,
      body: {
        person_id: pid,
        query: "下次生日想在家过",
        at: new Date().toISOString(),
        retrieval_mode: "local",
        trace: true,
      },
      description:
        "返回入口、关联路径、完整上下文及召回依据。此调用不会强化记忆。",
    },
    search: {
      method: "POST",
      path: `/spaces/${sid}/search`,
      body: {
        person_id: pid,
        query: "陨石一号",
        retrieval_mode: "local",
        top_k: 10,
        trace: true,
      },
      description: "只查入口候选；不展开关联，不返回正文。",
    },
    capabilities: {
      method: "GET",
      path: `/spaces/${sid}/capabilities`,
      body: null,
      description: "读取程序与大模型召回的实际可用状态。",
    },
    record: {
      method: "POST",
      path: `/spaces/${sid}/conversations/${cid || "CONVERSATION_ID"}/messages`,
      body: {
        role: "user",
        sender_id: pid || "SENDER_PERSON_ID",
        in_reply_to: null,
        mention_ids: [],
        content: "今天发生了一件小事…",
        client_id: "unique-message-id",
      },
      description:
        "群聊必须逐条填写 sender_id；回复填本服务消息 ID，@ 填人物 ID。使用稳定的 client_id 可避免重试重复消息。",
    },
    group: {
      method: "POST",
      path: `/spaces/${sid}/conversations`,
      body: {kind: "group", title: "朋友群"},
      description: "创建群会话。先在关注设置注册人物并绑定平台 UID，接入方维护平台身份到人物 ID 的映射。",
    },
    maintain: {
      method: "POST",
      path: `/spaces/${sid}/maintenance`,
      body: { conversation_id: cid || null },
      description:
        "异步维护事件，返回任务 ID。通过 jobs 接口读取状态与失败原因。",
    },
    monitoring: {
      method: "GET",
      path: "/monitoring",
      body: null,
      description:
        "返回请求、模型、召回、任务与存储指标的 JSON 快照。Prometheus 抓取入口为 /metrics，可单独配置监控令牌。",
    },
  };
  const item = samples[tab];
  const command = `curl -X ${item.method} '${origin}/api/v1${item.path}' \\\n  -H 'Authorization: Bearer YOUR_SERVICE_TOKEN'${item.method === "GET" ? "" : ` \\\n  -H 'Content-Type: application/json' \\\n  -d '${JSON.stringify(item.body, null, 2)}'`}`;
  return (
    <>
      <section className="page-heading">
        <div>
          <div className="eyebrow">
            <span />
            BUILT TO CONNECT
          </div>
          <h1>
            同一份记忆，自由接入<span>。</span>
          </h1>
          <p>前端与 agent 使用同一套 REST API，记忆服务独立部署。</p>
        </div>
        <a
          className="button secondary"
          href="/docs"
          target="_blank"
          rel="noreferrer"
        >
          <ExternalLink size={15} />
          交互式 API 文档
        </a>
      </section>
      <div className="api-intro">
        <span className="api-icon">
          <Code2 size={26} />
        </span>
        <div>
          <h3>先记录对话，再获取记忆上下文</h3>
          <p>
            把返回的 context 加入你自己的模型请求。回应方式始终由你的 agent
            决定。
          </p>
        </div>
        <span className="pill green">REST · JSON · OpenAPI</span>
      </div>
      <section className="panel api-example">
        <div className="tabs">
          {[
            ["context", "构造上下文"],
            ["search", "候选查找"],
            ["capabilities", "可用能力"],
            ["record", "记录对话"],
            ["group", "创建群聊"],
            ["maintain", "维护记忆"],
            ["monitoring", "监控指标"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="api-description">
          <span className="method-badge">{item.method}</span>
          <code>/api/v1{item.path}</code>
          <p>{item.description}</p>
        </div>
        <div className="code-toolbar">
          <span>cURL</span>
          <button onClick={() => copy(command)}>
            <Copy size={14} />
            复制示例
          </button>
        </div>
        <pre className="api-code">{command}</pre>
      </section>
      <div className="api-cards">
        <section className="panel">
          <Database size={21} />
          <h3>独立的记忆空间</h3>
          <p>
            不同 agent 使用不同
            space_id。人物、事件、会话与配置都属于明确的空间。
          </p>
          <code>{sid}</code>
        </section>
        <section className="panel">
          <GitBranch size={21} />
          <h3>保留可解释的唤起过程</h3>
          <p>
            context 接口同时返回 selected、candidates、path 和 omitted，便于你的
            agent 接入与调试。
          </p>
        </section>
        <section className="panel">
          <Layers3 size={21} />
          <h3>可自行部署</h3>
          <p>
            Docker 镜像同时提供后端 API 与构建后的 React 页面。SQLite
            使用独立持久化卷。
          </p>
          <button
            className="text-link"
            onClick={() => copy("docker compose up --build -d")}
          >
            复制启动命令
            <Copy size={13} />
          </button>
        </section>
      </div>
    </>
  );
}
