import { GitBranch, Timer } from "lucide-react";
import type { MonitoringSnapshot } from "./types";
import {
  count,
  duration,
  histogram,
  metric,
  percentile,
  samples,
} from "./monitoringMetrics";
import { modeName, routeNames, stageNames, routeStates } from "./retrievalUi";

export function RetrievalMonitoring({
  snapshot,
}: {
  snapshot: MonitoringSnapshot;
}) {
  const routes = [
    ...new Set(
      samples(snapshot, "memory_retrieval_route_duration_seconds_count").map(
        (sample) => sample.labels.route,
      ),
    ),
  ];
  const stages = [
    ...new Set(
      samples(snapshot, "memory_retrieval_stage_duration_seconds_count").map(
        (sample) => sample.labels.stage,
      ),
    ),
  ];
  const modes = [
    ...new Set(
      samples(snapshot, "memory_recall_mode_duration_seconds_count").map(
        (sample) => sample.labels.mode,
      ),
    ),
  ];
  return (
    <>
      <section className="panel monitor-panel">
        <h2>远程向量健康状态</h2>
        <p>请求 {count(metric(snapshot, "memory_embedding_requests_total"))} 次 · 平均 {duration(histogram(snapshot, "memory_embedding_duration_seconds").average)} · 开启熔断 {count(metric(snapshot, "memory_embedding_open_circuits"))} 个空间</p>
        <div className="monitor-table-scroll"><table className="monitor-table"><thead><tr><th>空间</th><th>状态</th><th>已索引 / 待索引</th><th>最近错误</th></tr></thead><tbody>
          {(snapshot.embedding ?? []).map(s => <tr key={s.space_id}><td>{s.name}</td><td>{!s.enabled ? "未启用" : s.circuit_open ? `冷却 ${s.retry_after_seconds} 秒` : "已启用"}</td><td>{s.indexed_chunks} / {s.pending_chunks}</td><td>{s.last_error ? (routeStates[s.last_error] ?? s.last_error) : "—"}</td></tr>)}
        </tbody></table></div>
        <p>向量不可用时自动使用程序召回；索引未完成的事件仍参与程序检索。</p>
      </section>
      <section className="panel monitor-panel">
        <div className="monitor-panel-title">
          <div>
            <h2>
              <Timer size={17} />
              各召回方式的总耗时
            </h2>
            <p>
              按实际执行方式统计；包含失败与取消。自动降级的结果计入程序召回。
            </p>
          </div>
        </div>
        {!modes.length ? (
          <div className="monitor-empty">等待首次召回。</div>
        ) : (
          <div className="monitor-table-scroll">
            <table className="monitor-table">
              <thead>
                <tr>
                  <th>召回方式</th>
                  <th>样本数</th>
                  <th>平均耗时</th>
                  <th>P95 估算</th>
                </tr>
              </thead>
              <tbody>
                {modes.map((mode) => {
                  const timing = histogram(
                    snapshot,
                    "memory_recall_mode_duration_seconds",
                    { mode },
                  );
                  return (
                    <tr key={mode}>
                      <td>{modeName(mode)}</td>
                      <td>{count(timing.count)}</td>
                      <td>{duration(timing.average)}</td>
                      <td>{percentile(timing.p95)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="panel monitor-panel">
        <div className="monitor-panel-title">
          <div>
            <h2>
              <GitBranch size={17} />
              多路召回的实际贡献
            </h2>
            <p>各程序路径与远程向量分别观察；同一入口可能命中多路，贡献数不能相加。</p>
          </div>
          <span className="monitor-small-tag">
            降级{" "}
            {count(metric(snapshot, "memory_retrieval_degradations_total"))} 次
          </span>
        </div>
        {!routes.length ? (
          <div className="monitor-empty">尚无分路数据；下一次召回后显示。</div>
        ) : (
          <div className="monitor-table-scroll">
            <table className="monitor-table">
              <thead>
                <tr>
                  <th>检索路径</th>
                  <th>执行次数</th>
                  <th>平均耗时</th>
                  <th>P95 估算</th>
                  <th>平均候选</th>
                  <th>平均入口覆盖</th>
                </tr>
              </thead>
              <tbody>
                {routes.map((route) => {
                  const timing = histogram(
                    snapshot,
                    "memory_retrieval_route_duration_seconds",
                    { route },
                  );
                  const candidates = histogram(
                    snapshot,
                    "memory_retrieval_route_candidates",
                    { route },
                  );
                  const contributions = histogram(
                    snapshot,
                    "memory_retrieval_route_contributions",
                    { route },
                  );
                  return (
                    <tr key={route}>
                      <td>{routeNames[route] ?? route}</td>
                      <td>{count(timing.count)}</td>
                      <td>{duration(timing.average)}</td>
                      <td>{percentile(timing.p95)}</td>
                      <td>{candidates.average?.toFixed(1) ?? "—"}</td>
                      <td>{contributions.average?.toFixed(1) ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="retrieval-monitor-grid">
        <section className="panel monitor-panel">
          <div className="monitor-panel-title">
            <div>
              <h2>
                <Timer size={17} />
                召回耗时分解
              </h2>
              <p>候选检索包含远程向量等待，各行不能直接相加。</p>
            </div>
          </div>
          {!stages.length ? (
            <div className="monitor-empty">等待首次召回。</div>
          ) : (
            <div className="monitor-table-scroll">
              <table className="monitor-table">
                <thead>
                  <tr>
                    <th>阶段</th>
                    <th>平均耗时</th>
                    <th>P95 估算</th>
                  </tr>
                </thead>
                <tbody>
                  {stages.map((stage) => {
                    const timing = histogram(
                      snapshot,
                      "memory_retrieval_stage_duration_seconds",
                      { stage },
                    );
                    return (
                      <tr key={stage}>
                        <td>{stageNames[stage] ?? stage}</td>
                        <td>{duration(timing.average)}</td>
                        <td>{percentile(timing.p95)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p className="monitor-chart-caption">
            模型排队平均{" "}
            {duration(
              histogram(snapshot, "memory_model_queue_wait_seconds").average,
            )}
            ；该时间包含在相应模型调用中。
          </p>
        </section>
      </div>
    </>
  );
}
