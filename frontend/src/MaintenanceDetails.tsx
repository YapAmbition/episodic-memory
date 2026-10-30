import { useState } from "react";
import type { Job } from "./types";
import { maintenanceSummary, outcomeName } from "./maintenanceUi";
import "./maintenance.css";

export function MaintenanceDetails({ job, onRetry }: { job: Job; onRetry?: (id: string) => Promise<void> }) {
  const [retrying, setRetrying] = useState(false);
  if (!job.waiting_for_context && !job.budget && !job.error_detail && !job.focus_summary && job.status !== "deferred" && job.status !== "failed") return null;
  const b = job.budget;
  return <details className="maintenance-details">
    <summary>{job.error_detail ? "查看失败详情 · " : ""}{maintenanceSummary(job)}</summary>
    {job.focus_summary?.enabled && <p className="maintenance-note">关注筛选：检查 {job.focus_summary.scanned ?? 0} 条 · 候选 {job.focus_summary.candidates ?? 0} 条 · 实际提交 {job.focus_summary.submitted ?? 0} 条 · 待线索 {job.focus_summary.waiting ?? 0} 条 · 长期保留来源 {job.focus_summary.retained ?? 0} 条 · 忽略 {job.focus_summary.ignored ?? 0} 条 · 暂缓 {job.focus_summary.deferred ?? 0} 条。未转为长期来源的临时原文到期清理。</p>}
    {job.error_detail && <div className="maintenance-deferred">
      <p>任务 ID：<code>{job.id}</code></p>
      <p>失败阶段：{({prepare:"准备材料",model:"调用模型",parse:"解析 JSON",validate:"验证输出字段",supplement:"补读材料",apply:"验证引用与保存"} as Record<string,string>)[job.error_detail.stage] || job.error_detail.stage} · {job.error_detail.exception_type}{job.error_detail.status_code ? ` · HTTP ${job.error_detail.status_code}` : ""}</p>
      {job.error_detail.issues?.map((issue, i) => <p key={i}><code>{issue.field || "输出根节点"}</code>：{issue.message}（{issue.code}）</p>)}
      {(job.error_detail.error_count ?? 0) > 20 && <p>共 {job.error_detail.error_count} 项问题，仅展示前 20 项。</p>}
      <p>在后端日志中搜索此任务 ID，可找到对应错误记录。</p>
    </div>}
    {job.run_call_limit && <p className="maintenance-note">本轮累计模型调用 {job.run_model_call_count ?? 0} / {job.run_call_limit} 次（含续批、重试和补读）。达到上限后暂停自动整理，需手动继续。</p>}
    {job.waiting_for_context && <p className="maintenance-note">{job.waiting_reason}；相同材料不会重复调用模型，其他会话可以继续整理。</p>}
    {b && <><div className="maintenance-facts">
      <span><small>容量来源</small>{b.source === "configured" ? "手动配置" : "未知 · 保守预算"}</span>
      <span><small>本次估算输入 / 上限</small>{b.estimated_input_tokens.toLocaleString()} / {b.input_limit.toLocaleString()} token</span>
      <span><small>输出预留 / 安全余量</small>{b.output_reserved.toLocaleString()} / {b.safety_reserved.toLocaleString()} token</span>
      <span><small>旧事材料</small>{b.included_count} 件进入 · {b.budget_omitted_count} 件预算省略</span>
      <span><small>遗忘与唤起</small>{b.faded_count} 件低权重省略 · {b.revived_count} 件被强线索唤起</span>
      <span><small>完成进度</small>{job.message_count} 条完整消息 · {job.fragment_count ?? 0} 个片段</span>
    </div>
    <p className="maintenance-note">输入按 UTF-8 保守估算，并非提供方实际 token 用量。后台整理和补读不会强化记忆。</p>
    {job.fragments?.filter(f => f.start > 0 || f.end < f.total).map(f => <p className="maintenance-note" key={f.message_id}>长消息：本次范围 {f.start.toLocaleString()}–{f.end.toLocaleString()} / {f.total.toLocaleString()} 字符；成功提交后推进。</p>)}
    {job.force_progress && <p className="maintenance-note">本批达到容量上限，已要求整理阶段性事实或明确跳过。</p>}
    {!!job.ignored_messages?.length && <p className="maintenance-note">跳过 {job.ignored_messages.length} 条：{[...new Set(job.ignored_messages.map(m => m.reason))].join("；")}</p>}
    {!!job.deferred_messages?.length && <div className="maintenance-deferred">暂缓 {job.deferred_messages.length} 条消息：{[...new Set(job.deferred_messages.map(m => m.reason))].join("；")}</div>}
    {!!job.candidates?.length && <div className="maintenance-candidate-list">
      {job.candidates.map(c => <div key={c.event_id}><span>{c.title}<small>{c.included ? "已提供" : "预算省略"}{c.revived ? " · 强线索唤起" : ""}</small></span><span>鲜明度 {(c.strength * 100).toFixed(1)}%<small>整理分数 {c.score.toFixed(3)}</small></span></div>)}
    </div>}
    {!!job.attempts?.length && <div className="maintenance-attempts">{job.attempts.map((a, i) => <span key={i}>第 {i + 1} 次 · {outcomeName(a.outcome)} · 估算 {a.estimated_input_tokens.toLocaleString()} token{a.elapsed_ms == null ? "" : ` · ${(a.elapsed_ms / 1000).toFixed(2)} 秒`}</span>)}</div>}
    </>}
    {(job.status === "failed" || job.status === "deferred") && onRetry && <button className="button small" disabled={retrying} onClick={async () => { setRetrying(true); try { await onRetry(job.id); } finally { setRetrying(false); } }}>{retrying ? "正在提交…" : job.status === "deferred" ? "继续整理剩余消息" : "重试剩余消息"}</button>}
    {job.continuation_job_id && <p className="maintenance-note">剩余材料已创建后续批次，可在任务列表查看。</p>}
  </details>;
}
