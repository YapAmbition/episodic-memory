import { Brain, Gauge } from "lucide-react";
import type { MonitoringSnapshot } from "./types";
import { count, duration, histogram, metric, percentile } from "./monitoringMetrics";
import "./maintenance.css";

export function MaintenanceMonitoring({ snapshot }: { snapshot: MonitoringSnapshot }) {
  const input = histogram(snapshot, "memory_maintenance_estimated_input_tokens");
  const omissions = (reason: string) => metric(snapshot, "memory_maintenance_candidate_omissions_total", { reason });
  return <section className="panel monitor-panel">
    <div className="monitor-panel-title"><div><h2><Brain size={18} /> 整理预算与遗忘</h2><p>观察材料如何进入上下文，以及超限后的恢复。强线索唤起不会自动强化记忆。</p></div><Gauge size={19} /></div>
    <div className="maintenance-monitor-kpis">
      <div><span>平均估算输入</span><strong>{input.average == null ? "—" : count(Math.round(input.average))}</strong><small>token / 模型调用 · 非实际用量</small></div>
      <div><span>自动缩批</span><strong>{count(metric(snapshot, "memory_maintenance_adaptations_total"))}</strong><small>上下文超限 {count(metric(snapshot, "memory_maintenance_adaptations_total", { reason: "context_limit" }))} · 输出截断 {count(metric(snapshot, "memory_maintenance_adaptations_total", { reason: "output_limit" }))}</small></div>
      <div><span>强线索唤起旧事</span><strong>{count(metric(snapshot, "memory_maintenance_revived_candidates_total"))}</strong><small>按最终批次计数，可重复出现</small></div>
      <div><span>暂缓消息</span><strong>{count(metric(snapshot, "memory_maintenance_deferred_messages_total"))}</strong><small>材料不足，保留原文与未完成进度</small></div>
    </div>
    <div className="maintenance-monitor-kpis">{[["scanned","关注筛选检查"],["candidates","相关候选"],["retained","长期保留来源"],["expired","到期清理消息"]].map(([key,label]) => <div key={key}><span>{label}</span><strong>{count(metric(snapshot,"memory_focus_messages_total",{outcome:key}))}</strong><small>按处理批次累计，重复检查会计数</small></div>)}</div>
    <div className="monitor-table-scroll"><table className="monitor-table"><thead><tr><th>整理阶段</th><th>样本数</th><th>平均耗时</th><th>P95 估算</th></tr></thead><tbody>{[["prepare", "材料准备"], ["model", "模型请求（含排队）"], ["commit", "校验后写入"]].map(([stage, label]) => { const h = histogram(snapshot, "memory_maintenance_stage_duration_seconds", { stage }); return <tr key={stage}><td>{label}</td><td>{count(h.count)}</td><td>{duration(h.average)}</td><td>{percentile(h.p95)}</td></tr>; })}</tbody></table></div>
    <p className="maintenance-note">候选省略：低权重 {count(omissions("faded"))} · 预算不足 {count(omissions("budget"))} · 数量限制 {count(omissions("candidate_limit"))} · 关联弱 {count(omissions("unrelated"))}。统计按最终批次累计，进程重启后归零。</p>
    <p className="maintenance-note">已提交 {count(metric(snapshot, "memory_maintenance_committed_fragments_total"))} 个消息片段。完整消息完成数与片段数分别统计；详细预算、补读和重试记录见实验室的整理任务。</p>
  </section>;
}
