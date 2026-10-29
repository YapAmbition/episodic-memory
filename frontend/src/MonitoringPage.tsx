import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  ArrowDownLeft,
  ArrowUpRight,
  Check,
  CircleAlert,
  Clock3,
  Copy,
  Cpu,
  Database,
  ExternalLink,
  GitBranch,
  Layers3,
  Loader2,
  RefreshCw,
  Search,
  Server,
  Sparkles,
  Timer,
  WifiOff,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "./api";
import type { MonitoringSnapshot } from "./types";
import {
  assertSnapshot,
  bytes,
  count,
  duration,
  histogram,
  httpRoutes,
  httpSummary,
  metric,
  modelMetrics,
  observe,
  percentile,
  rate,
  samples,
  uptime,
} from "./monitoringMetrics";
import type { Observation } from "./monitoringMetrics";
import "./monitoring.css";
import { MaintenanceMonitoring } from "./MaintenanceMonitoring";
import { RetrievalMonitoring } from "./RetrievalMonitoring";

const purposeNames: Record<string, string> = {
  maintenance: "事件整理",
  chat: "模拟对话",
  connection_test: "连接测试",
  other: "其他调用",
};
const time = (at: number) =>
  new Date(at).toLocaleTimeString("zh-CN", { hour12: false });

function Kpi({
  icon: Icon,
  label,
  value,
  unit,
  children,
  tone = "sage",
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  unit?: string;
  children: ReactNode;
  tone?: string;
}) {
  return (
    <section className={`monitor-kpi ${tone}`}>
      <div className="monitor-kpi-label">
        <span>{label}</span>
        <Icon size={17} />
      </div>
      <div className="monitor-kpi-value">
        {value}
        <small>{unit}</small>
      </div>
      <div className="monitor-kpi-foot">{children}</div>
    </section>
  );
}

function PanelTitle({
  icon: Icon,
  title,
  note,
  children,
}: {
  icon: LucideIcon;
  title: string;
  note: string;
  children?: ReactNode;
}) {
  return (
    <div className="monitor-panel-title">
      <div>
        <h2>
          <Icon size={17} />
          {title}
        </h2>
        <p>{note}</p>
      </div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="monitor-empty">
      <span>
        <Activity size={23} />
      </span>
      {children}
    </div>
  );
}

function TrafficChart({ history }: { history: Observation[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const valid = history.some((point) => point.requestsPerSecond !== null);
  const start = history[0]?.at ?? 0,
    end = Math.max(start + 1000, history.at(-1)?.at ?? start);
  const maximum =
    Math.max(
      1,
      ...history.flatMap((point) => [
        point.requestsPerSecond ?? 0,
        point.recallsPerSecond ?? 0,
      ]),
    ) * 1.1;
  const x = (point: Observation) =>
    45 + ((point.at - start) / (end - start)) * 655;
  const y = (value: number) => 196 - (value / maximum) * 158;
  const series = (key: "requestsPerSecond" | "recallsPerSecond") => {
    let connected = false;
    return history
      .map((point) => {
        if (point[key] === null) {
          connected = false;
          return "";
        }
        const command = connected ? "L" : "M";
        connected = true;
        return `${command}${x(point)},${y(point[key]!)}`;
      })
      .join(" ");
  };
  const selected =
    hover !== null ? history[Math.min(hover, history.length - 1)] : null;
  const latest = history.at(-1);
  return (
    <div className="monitor-chart-wrap">
      <div className="monitor-chart-legend">
        <span>
          <i className="sage" />
          HTTP 请求{" "}
          <b>
            {latest?.requestsPerSecond == null
              ? "—"
              : latest.requestsPerSecond.toFixed(2)}
          </b>
          <small>次/秒</small>
        </span>
        <span>
          <i className="copper" />
          记忆召回{" "}
          <b>
            {latest?.recallsPerSecond == null
              ? "—"
              : latest.recallsPerSecond.toFixed(2)}
          </b>
          <small>次/秒</small>
        </span>
      </div>
      <div className="monitor-chart">
        <svg
          ref={svg}
          viewBox="0 0 740 240"
          role="img"
          aria-label="本页采样的 HTTP 请求和记忆召回速率，可用左右方向键查看采样点"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              setHover((index) =>
                Math.max(
                  0,
                  Math.min(
                    history.length - 1,
                    (index ?? history.length - 1) +
                      (event.key === "ArrowLeft" ? -1 : 1),
                  ),
                ),
              );
            }
          }}
          onBlur={() => setHover(null)}
          onPointerLeave={() => setHover(null)}
          onPointerMove={(event) => {
            if (!svg.current || !history.length) return;
            const rect = svg.current.getBoundingClientRect();
            const target =
              start +
              Math.max(
                0,
                Math.min(
                  1,
                  (((event.clientX - rect.left) / rect.width) * 740 - 45) / 655,
                ),
              ) *
                (end - start);
            const closest = history.reduce(
              (best, point, index) =>
                Math.abs(point.at - target) <
                Math.abs(history[best].at - target)
                  ? index
                  : best,
              0,
            );
            setHover(closest);
          }}
        >
          {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
            <g key={tick}>
              <line
                x1="45"
                x2="700"
                y1={y(maximum * tick)}
                y2={y(maximum * tick)}
                stroke="#e7e9e1"
                strokeDasharray="3 5"
              />
              <text x="34" y={y(maximum * tick) + 4} textAnchor="end">
                {(maximum * tick).toFixed(maximum < 5 ? 1 : 0)}
              </text>
            </g>
          ))}
          {valid && (
            <>
              <path
                d={series("requestsPerSecond")}
                className="monitor-line sage"
              />
              <path
                d={series("recallsPerSecond")}
                className="monitor-line copper"
              />
              {history.map(
                (point, index) =>
                  point.requestsPerSecond !== null && (
                    <circle
                      key={index}
                      cx={x(point)}
                      cy={y(point.requestsPerSecond)}
                      r="2.8"
                      fill="#718774"
                    />
                  ),
              )}
            </>
          )}
          {selected && selected.requestsPerSecond !== null && (
            <line
              x1={x(selected)}
              x2={x(selected)}
              y1="28"
              y2="196"
              stroke="#a8b4a4"
              strokeDasharray="3 4"
            />
          )}
          <text x="45" y="224">
            {start ? time(start) : "等待采样"}
          </text>
          <text x="700" y="224" textAnchor="end">
            {history.length > 1
              ? time(history.at(-1)!.at)
              : "下一次刷新后开始绘制"}
          </text>
        </svg>
        {!valid && (
          <div className="monitor-chart-placeholder">
            <Activity size={23} />
            <strong>正在收集采样点</strong>
            <span>累计指标已就绪，速率需要至少两次有效采样。</span>
          </div>
        )}
        {selected && valid && (
          <div
            className="monitor-chart-tooltip"
            style={{
              left: `${Math.max(18, Math.min(79, (x(selected) / 740) * 100))}%`,
            }}
          >
            <b>{time(selected.at)}</b>
            <span>
              HTTP：{selected.requestsPerSecond?.toFixed(2) ?? "—"} 次/秒
            </span>
            <span>
              召回：{selected.recallsPerSecond?.toFixed(2) ?? "—"} 次/秒
            </span>
          </div>
        )}
      </div>
      <p className="monitor-chart-caption">
        显示本页最近 5 分钟采样；切换页面会重新采样，断线区间不连线。
      </p>
    </div>
  );
}

export function MonitoringPage({
  onConnectionChange,
}: {
  onConnectionChange?: (connected: boolean) => void;
}) {
  const [snapshot, setSnapshot] = useState<MonitoringSnapshot | null>(null);
  const [history, setHistory] = useState<Observation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [interval, setIntervalSeconds] = useState(5);
  const [restarted, setRestarted] = useState(false);
  const [routeQuery, setRouteQuery] = useState("");
  const [errorOnly, setErrorOnly] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const mounted = useRef(false),
    inFlight = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const historyRef = useRef<Observation[]>([]);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setLoading(true);
    const current = new AbortController();
    controller.current = current;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      current.abort();
    }, 10000);
    try {
      const next = await api<MonitoringSnapshot>("/monitoring", {
        signal: current.signal,
      });
      assertSnapshot(next);
      if (!mounted.current) return;
      const received = Date.now();
      const observation = observe(historyRef.current, next, received);
      historyRef.current = observation.history;
      setHistory(observation.history);
      if (observation.reset) setRestarted(true);
      setSnapshot(next);
      setUpdatedAt(received);
      setError("");
      onConnectionChange?.(true);
    } catch (cause) {
      if (mounted.current) {
        setError(
          timedOut
            ? "指标请求超时，请检查服务状态后重试。"
            : cause instanceof Error
              ? cause.message
              : "暂时无法获取指标",
        );
        onConnectionChange?.(false);
      }
    } finally {
      clearTimeout(timeout);
      inFlight.current = false;
      if (mounted.current) setLoading(false);
    }
  }, [onConnectionChange]);

  useEffect(() => {
    mounted.current = true;
    refresh();
    return () => {
      mounted.current = false;
      controller.current?.abort();
      if (copyTimer.current) clearTimeout(copyTimer.current);
    };
  }, [refresh]);
  useEffect(() => {
    if (!interval) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) refresh();
    }, interval * 1000);
    const resume = () => {
      if (!document.hidden) refresh();
    };
    document.addEventListener("visibilitychange", resume);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [interval, refresh]);

  const view = useMemo(() => {
    if (!snapshot) return null;
    const http = httpSummary(snapshot),
      models = modelMetrics(snapshot);
    const recalls = {
      success: metric(snapshot, "memory_recalls_total", { outcome: "success" }),
      empty: metric(snapshot, "memory_recalls_total", { outcome: "empty" }),
      error: metric(snapshot, "memory_recalls_total", { outcome: "error" }),
      latency: histogram(snapshot, "memory_recall_duration_seconds"),
    };
    return {
      http,
      models,
      recalls,
      routes: httpRoutes(snapshot),
      modelCalls: models.reduce((sum, item) => sum + item.total, 0),
      modelFailures: models.reduce(
        (sum, item) => sum + item.total - item.success,
        0,
      ),
      modelInput: metric(snapshot, "memory_model_tokens_total", {
        direction: "input",
      }),
      modelOutput: metric(snapshot, "memory_model_tokens_total", {
        direction: "output",
      }),
      modelHasUsage: samples(snapshot, "memory_model_tokens_total").length > 0,
      modelInputReported:
        samples(snapshot, "memory_model_tokens_total", { direction: "input" })
          .length > 0,
      modelOutputReported:
        samples(snapshot, "memory_model_tokens_total", { direction: "output" })
          .length > 0,
      missingUsage: metric(snapshot, "memory_model_usage_missing_total"),
      selection: histogram(snapshot, "memory_recall_events", {
        stage: "selected",
      }),
      context: histogram(snapshot, "memory_context_characters"),
      jobLatency: histogram(snapshot, "memory_maintenance_duration_seconds"),
      queueLatency: histogram(
        snapshot,
        "memory_maintenance_queue_wait_seconds",
      ),
    };
  }, [snapshot]);
  const totalRecalls = view
    ? view.recalls.success + view.recalls.empty + view.recalls.error
    : 0;
  const jobs = snapshot?.storage.jobs ?? {};
  const records = snapshot?.storage.records ?? {};
  const filteredRoutes =
    view?.routes.filter(
      (item) =>
        (item.method + " " + item.route)
          .toLowerCase()
          .includes(routeQuery.toLowerCase()) &&
        (!errorOnly || item.clientErrors + item.serverErrors > 0),
    ) ?? [];

  return (
    <div className="monitoring-page">
      <section className="page-heading monitor-heading">
        <div>
          <div className="eyebrow">
            <span />
            SERVICE OBSERVATORY
          </div>
          <h1>
            记忆在运转，状态看得见<span>。</span>
          </h1>
          <p>从一次请求，到一段记忆的浮现。整个服务的运行指标，都在这里。</p>
        </div>
        <div className="monitor-controls">
          <label>
            <span>自动刷新</span>
            <select
              aria-label="监控自动刷新频率"
              value={interval}
              onChange={(event) =>
                setIntervalSeconds(Number(event.target.value))
              }
            >
              <option value={5}>每 5 秒</option>
              <option value={15}>每 15 秒</option>
              <option value={30}>每 30 秒</option>
              <option value={0}>暂停</option>
            </select>
          </label>
          <button
            className="button secondary"
            disabled={loading}
            onClick={refresh}
          >
            <RefreshCw size={15} className={loading ? "spin" : ""} />
            {loading ? "刷新中" : "刷新指标"}
          </button>
        </div>
      </section>

      <div className="monitor-scope">
        <span
          className={`monitor-connection ${error ? "bad" : snapshot ? "good" : ""}`}
        >
          <i />
          {error ? "连接异常" : snapshot ? "指标已连接" : "正在连接"}
        </span>
        <span>
          <Server size={13} />
          全部记忆空间
        </span>
        <span>
          <Clock3 size={13} />
          {snapshot
            ? `已运行 ${uptime(snapshot.uptime_seconds)}`
            : "等待服务数据"}
        </span>
        <span className="monitor-updated">
          {updatedAt
            ? `${error ? "上次成功更新" : "更新于"} ${time(updatedAt)}`
            : "尚未采样"}
        </span>
      </div>
      {error && (
        <div className="monitor-alert" role="alert">
          <WifiOff size={18} />
          <div>
            <strong>暂时无法刷新指标</strong>
            <p>
              {error}
              {snapshot
                ? " 当前保留上次成功获取的数据。"
                : " 请确认新版后端已启动，并检查访问令牌。"}
            </p>
          </div>
          <button className="text-link" disabled={loading} onClick={refresh}>
            重试
            <RefreshCw size={13} />
          </button>
        </div>
      )}
      {restarted && (
        <div className="monitor-reset" role="status">
          <RefreshCw size={14} />
          检测到服务重启或计数器重置，本页趋势已重新开始采样。
          <button aria-label="关闭重启提示" onClick={() => setRestarted(false)}>
            知道了
          </button>
        </div>
      )}

      {!snapshot || !view ? (
        <div className="panel monitor-initial">
          {loading ? (
            <>
              <Loader2 className="spin" size={26} />
              <h2>正在读取运行状态</h2>
              <p>指标来自后端，不需要连接模型。</p>
            </>
          ) : (
            <>
              <WifiOff size={28} />
              <h2>等待服务连接</h2>
              <p>启动服务后，点击“刷新指标”即可查看。</p>
            </>
          )}
        </div>
      ) : (
        <>
          <div className="monitor-kpis">
            <Kpi
              icon={Activity}
              label="HTTP 请求"
              value={count(view.http.total)}
              unit="次"
            >
              <span>
                5xx 占比{" "}
                <b className={view.http.serverErrors ? "negative" : ""}>
                  {rate(view.http.serverErrorRate)}
                </b>
              </span>
              <span>P95 {percentile(view.http.latency.p95)}</span>
            </Kpi>
            <Kpi
              icon={Sparkles}
              label="生成式模型调用"
              value={count(view.modelCalls)}
              unit="次"
              tone="copper"
            >
              <span>
                失败{" "}
                <b className={view.modelFailures ? "negative" : ""}>
                  {count(view.modelFailures)}
                </b>{" "}
                次
              </span>
              <span>
                {view.modelHasUsage
                  ? count(view.modelInput + view.modelOutput) + " tokens 已上报"
                  : view.modelCalls
                    ? "token 用量未上报"
                    : "尚无模型调用"}
              </span>
            </Kpi>
            <Kpi
              icon={GitBranch}
              label="记忆召回"
              value={count(totalRecalls)}
              unit="次"
            >
              <span>
                有结果 <b>{count(view.recalls.success)}</b>
              </span>
              <span>
                空结果 <b>{count(view.recalls.empty)}</b>
              </span>
            </Kpi>
            <Kpi
              icon={Layers3}
              label="待整理消息"
              value={count(snapshot.storage.pending_messages)}
              unit="条"
              tone="sand"
            >
              <span>
                运行中 <b>{count(jobs.running ?? 0)}</b>
              </span>
              <span>
                排队 <b>{count(jobs.queued ?? 0)}</b>
              </span>
            </Kpi>
          </div>
          <div className="monitor-period-note">
            <Clock3 size={12} />
            请求、调用和耗时统计为本次运行累计；待整理消息与任务状态来自数据库。
          </div>

          <div className="monitor-chart-grid">
            <section className="panel monitor-panel traffic-panel">
              <PanelTitle
                icon={Activity}
                title="服务脉搏"
                note="相邻有效采样的计数增量 ÷ 实际间隔"
              >
                <span className="monitor-small-tag">
                  {interval ? `${interval}s 采样` : "手动采样"}
                </span>
              </PanelTitle>
              <TrafficChart history={history} />
            </section>
            <section className="panel monitor-panel recall-health">
              <PanelTitle
                icon={GitBranch}
                title="记忆如何浮现"
                note="空召回也可能是合理结果"
              />
              <div
                className="monitor-distribution"
                aria-label={`有结果 ${view.recalls.success} 次，空结果 ${view.recalls.empty} 次，失败 ${view.recalls.error} 次`}
              >
                {totalRecalls ? (
                  <>
                    <span
                      className="sage"
                      style={{
                        width: `${(view.recalls.success / totalRecalls) * 100}%`,
                      }}
                    />
                    <span
                      className="sand"
                      style={{
                        width: `${(view.recalls.empty / totalRecalls) * 100}%`,
                      }}
                    />
                    <span
                      className="rose"
                      style={{
                        width: `${(view.recalls.error / totalRecalls) * 100}%`,
                      }}
                    />
                  </>
                ) : (
                  <span className="no-data" />
                )}
              </div>
              <div className="monitor-outcomes">
                {[
                  ["有结果", view.recalls.success, "sage"],
                  ["空结果", view.recalls.empty, "sand"],
                  ["失败", view.recalls.error, "rose"],
                ].map(([label, value, tone]) => (
                  <div key={label}>
                    <span>
                      <i className={String(tone)} />
                      {label}
                    </span>
                    <b>
                      {count(Number(value))}
                      <small>
                        {rate(
                          totalRecalls ? Number(value) / totalRecalls : null,
                        )}
                      </small>
                    </b>
                  </div>
                ))}
              </div>
              <div className="monitor-mini-grid">
                <div>
                  <span>召回 P95 估算</span>
                  <b>{percentile(view.recalls.latency.p95)}</b>
                </div>
                <div>
                  <span>平均选中事件</span>
                  <b>
                    {view.selection.average?.toFixed(1) ?? "—"}
                    <small>件 / 次</small>
                  </b>
                </div>
                <div>
                  <span>平均上下文</span>
                  <b>
                    {view.context.average === null
                      ? "—"
                      : count(view.context.average)}
                    <small>字符 / 次</small>
                  </b>
                </div>
                <div>
                  <span>平均召回耗时</span>
                  <b>{duration(view.recalls.latency.average)}</b>
                </div>
              </div>
            </section>
          </div>

          <RetrievalMonitoring snapshot={snapshot} />
          <MaintenanceMonitoring snapshot={snapshot} />
          <section className="panel monitor-panel monitor-models">
            <PanelTitle
              icon={Sparkles}
              title="生成式模型调用与用量"
              note="逻辑调用与 HTTP 尝试分别计数，尝试数包含重试"
            >
              <span className="monitor-small-tag">本次运行</span>
            </PanelTitle>
            <div className="monitor-token-strip">
              <span>
                <ArrowDownLeft size={15} />
                输入{" "}
                <b>{view.modelInputReported ? count(view.modelInput) : "—"}</b>
                <small>tokens</small>
              </span>
              <span>
                <ArrowUpRight size={15} />
                输出{" "}
                <b>
                  {view.modelOutputReported ? count(view.modelOutput) : "—"}
                </b>
                <small>tokens</small>
              </span>
              <span className="monitor-token-note">
                {view.missingUsage ? (
                  <>
                    <CircleAlert size={13} />
                    {count(view.missingUsage)}{" "}
                    次成功调用未上报用量，以上仅为已上报部分。
                  </>
                ) : (
                  "仅统计服务商实际返回的用量，不估算费用。"
                )}
              </span>
            </div>
            {!view.models.length ? (
              <Empty>
                <strong>还没有模型调用</strong>
                <p>使用整理或模拟对话后，这里会显示对应的调用与用量。向量请求在远程向量面板单独统计。</p>
              </Empty>
            ) : (
              <div className="monitor-table-scroll">
                <table className="monitor-table">
                  <thead>
                    <tr>
                      <th>调用用途</th>
                      <th>调用 / 尝试</th>
                      <th>成功率</th>
                      <th>输入 / 输出 tokens</th>
                      <th>平均耗时</th>
                      <th>P95 估算</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.models.map((item) => (
                      <tr key={item.purpose}>
                        <td>
                          <strong>
                            {purposeNames[item.purpose] ?? item.purpose}
                          </strong>
                          <small>{item.purpose}</small>
                        </td>
                        <td>
                          {count(item.total)}
                          <span className="monitor-cell-muted">
                            {" "}
                            / {count(item.attempts)}
                          </span>
                        </td>
                        <td>
                          <span
                            className={
                              item.success < item.total
                                ? "monitor-warning-text"
                                : "monitor-success-text"
                            }
                          >
                            {rate(
                              item.total ? item.success / item.total : null,
                            )}
                          </span>
                        </td>
                        <td>
                          {item.hasUsage ? (
                            <>
                              {item.inputReported ? count(item.input) : "—"}
                              <span className="monitor-cell-muted">
                                {" "}
                                /{" "}
                                {item.outputReported ? count(item.output) : "—"}
                              </span>
                              {!!item.missingUsage && (
                                <small>
                                  另有 {count(item.missingUsage)} 次未上报
                                </small>
                              )}
                            </>
                          ) : (
                            <span className="monitor-cell-muted">未上报</span>
                          )}
                        </td>
                        <td>{duration(item.latency.average)}</td>
                        <td>{percentile(item.latency.p95)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <div className="monitor-bottom-grid">
            <section className="panel monitor-panel">
              <PanelTitle
                icon={Layers3}
                title="整理与记忆维护"
                note="任务状态包含数据库中的历史任务"
              />
              <div className="monitor-job-states">
                {[
                  ["运行中", "running"],
                  ["排队中", "queued"],
                  ["已完成", "completed"],
                  ["失败", "failed"],
                  ["已跳过", "skipped"],
                  ["待补充材料", "deferred"],
                ].map(([name, state]) => (
                  <div key={state}>
                    <span className={`monitor-job-dot ${state}`} />
                    <span>{name}</span>
                    <b>{count(jobs[state] ?? 0)}</b>
                  </div>
                ))}
              </div>
              <div className="monitor-mini-grid">
                <div>
                  <span>整理执行 P95</span>
                  <b>{percentile(view.jobLatency.p95)}</b>
                </div>
                <div>
                  <span>排队 P95</span>
                  <b>{percentile(view.queueLatency.p95)}</b>
                </div>
                <div>
                  <span>本次已处理消息</span>
                  <b>
                    {count(metric(snapshot, "memory_processed_messages_total"))}
                    <small>条</small>
                  </b>
                </div>
                <div>
                  <span>调度异常</span>
                  <b
                    className={
                      metric(snapshot, "memory_scheduler_errors_total")
                        ? "negative"
                        : ""
                    }
                  >
                    {count(metric(snapshot, "memory_scheduler_errors_total"))}
                    <small>次</small>
                  </b>
                </div>
              </div>
              <div className="monitor-inline-note">
                <GitBranch size={14} />
                真实重提强化{" "}
                <b>
                  {count(metric(snapshot, "memory_reinforcements_total"))}
                </b>{" "}
                次
                <span>
                  显式{" "}
                  {count(
                    metric(snapshot, "memory_reinforcements_total", {
                      origin: "explicit",
                    }),
                  )}{" "}
                  · 整理确认{" "}
                  {count(
                    metric(snapshot, "memory_reinforcements_total", {
                      origin: "maintenance",
                    }),
                  )}
                </span>
              </div>
            </section>
            <section className="panel monitor-panel">
              <PanelTitle
                icon={Database}
                title="存储与进程"
                note="数据库大小包含 SQLite 主文件与 WAL"
              />
              <div className="monitor-storage-lead">
                <span>
                  <Database size={20} />
                </span>
                <div>
                  <strong>{bytes(snapshot.storage.database_bytes)}</strong>
                  <p>记忆数据库</p>
                </div>
                <span className="monitor-small-tag">持久化</span>
              </div>
              <div className="monitor-records">
                {[
                  ["记忆空间", "space"],
                  ["事件", "event"],
                  ["原始消息", "message"],
                  ["记忆片段", "fragment"],
                ].map(([label, kind]) => (
                  <div key={kind}>
                    <span>{label}</span>
                    <b>{count(records[kind] ?? 0)}</b>
                  </div>
                ))}
              </div>
              <div className="monitor-resource-row">
                <span>
                  <Cpu size={14} />
                  进程内存
                </span>
                <b>
                  {bytes(
                    samples(snapshot, "process_resident_memory_bytes").length
                      ? metric(snapshot, "process_resident_memory_bytes")
                      : null,
                  )}
                </b>
              </div>
              <div className="monitor-resource-row">
                <span>
                  <Timer size={14} />
                  CPU 累计时间
                </span>
                <b>
                  {samples(snapshot, "process_cpu_seconds_total").length
                    ? duration(metric(snapshot, "process_cpu_seconds_total"))
                    : "未提供"}
                </b>
              </div>
              <div className="monitor-resource-row">
                <span>正在处理的 HTTP 请求</span>
                <b>
                  {count(metric(snapshot, "memory_http_requests_in_progress"))}
                </b>
              </div>
            </section>
          </div>

          <section className="panel monitor-panel monitor-routes">
            <PanelTitle
              icon={Activity}
              title="接口请求明细"
              note="按路由模板统计，不包含用户和事件的实际 ID"
            >
              <div className="monitor-route-tools">
                <label className="monitor-search">
                  <Search size={14} />
                  <input
                    aria-label="筛选接口路由"
                    placeholder="筛选接口…"
                    value={routeQuery}
                    onChange={(event) => setRouteQuery(event.target.value)}
                  />
                </label>
                <button
                  className={`monitor-filter ${errorOnly ? "active" : ""}`}
                  aria-pressed={errorOnly}
                  onClick={() => setErrorOnly((value) => !value)}
                >
                  仅看 4xx / 5xx
                </button>
              </div>
            </PanelTitle>
            {!filteredRoutes.length ? (
              <Empty>
                <strong>
                  {routeQuery || errorOnly
                    ? "没有匹配的接口"
                    : "还没有请求记录"}
                </strong>
                <p>
                  {errorOnly
                    ? "当前筛选条件下没有 4xx 或 5xx 记录。"
                    : "请求发生后，路由次数和耗时会自动更新。"}
                </p>
              </Empty>
            ) : (
              <div className="monitor-table-scroll">
                <table className="monitor-table">
                  <thead>
                    <tr>
                      <th>方法 / 路由</th>
                      <th>请求数</th>
                      <th>4xx</th>
                      <th>5xx</th>
                      <th>平均耗时</th>
                      <th>P95 估算</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRoutes.map((item) => (
                      <tr key={item.method + item.route}>
                        <td>
                          <div className="monitor-route-name">
                            <span
                              className={`monitor-method ${item.method.toLowerCase()}`}
                            >
                              {item.method}
                            </span>
                            <code>{item.route}</code>
                          </div>
                        </td>
                        <td>{count(item.total)}</td>
                        <td
                          className={
                            item.clientErrors
                              ? "monitor-warning-text"
                              : "monitor-cell-muted"
                          }
                        >
                          {count(item.clientErrors)}
                        </td>
                        <td
                          className={
                            item.serverErrors
                              ? "negative"
                              : "monitor-cell-muted"
                          }
                        >
                          {count(item.serverErrors)}
                        </td>
                        <td>{duration(item.latency.average)}</td>
                        <td>{percentile(item.latency.p95)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="monitor-table-foot">
              监控接口自身的轮询不计入请求指标。P95
              基于直方图桶估算；样本超出最大桶时展示下界。
            </div>
          </section>

          <section className="monitor-feedback-strip">
            <span>
              <Check size={15} />
              人工召回反馈
            </span>
            <span>
              有用{" "}
              <b>
                {count(
                  metric(snapshot, "memory_recall_feedback_total", {
                    verdict: "useful",
                  }),
                )}
              </b>
            </span>
            <span>
              无关{" "}
              <b>
                {count(
                  metric(snapshot, "memory_recall_feedback_total", {
                    verdict: "irrelevant",
                  }),
                )}
              </b>
            </span>
            <span>
              漏召回{" "}
              <b>
                {count(
                  metric(snapshot, "memory_recall_feedback_total", {
                    verdict: "missed",
                  }),
                )}
              </b>
            </span>
            <small>按标注计数，不作为自动质量评分。</small>
          </section>

          <details className="panel monitor-raw">
            <summary>
              <span>
                <Activity size={15} />
                原始指标快照
              </span>
              <small>{snapshot.metrics.length} 组指标 · JSON</small>
            </summary>
            <div className="monitor-raw-tools">
              <span>仅包含当前采样的指标数据</span>
              <button
                className="text-link"
                onClick={async () => {
                  try {
                    setCopyError("");
                    await navigator.clipboard.writeText(
                      JSON.stringify(snapshot, null, 2),
                    );
                    setCopied(true);
                    if (copyTimer.current) clearTimeout(copyTimer.current);
                    copyTimer.current = setTimeout(
                      () => setCopied(false),
                      2000,
                    );
                  } catch {
                    setCopied(false);
                    setCopyError("复制失败，请手动选择下方内容。");
                  }
                }}
              >
                {copied ? <Check size={13} /> : <Copy size={13} />}
                {copied ? "已复制" : "复制快照"}
              </button>
              <a href="/metrics" target="_blank" rel="noreferrer">
                Prometheus 端点
                <ExternalLink size={12} />
              </a>
            </div>
            {copyError && (
              <p className="monitor-copy-error" role="status">
                {copyError}
              </p>
            )}
            <pre tabIndex={0}>{JSON.stringify(snapshot, null, 2)}</pre>
          </details>
          <p className="monitor-footer-note">
            整个服务统一统计，切换记忆空间不会改变指标。计数器在进程重启后归零；长期历史可接入
            Prometheus 保存。
          </p>
        </>
      )}
    </div>
  );
}
