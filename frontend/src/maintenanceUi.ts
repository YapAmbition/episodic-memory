import type { Job, Config, ModelConfig } from "./types";

export function maintenanceSummary(job: Job) {
  return `${job.model_call_count ?? 0} 次模型调用 · ${job.adaptation_count ?? 0} 次缩批 · ${job.read_rounds ?? 0} 次补读`;
}
export function outcomeName(outcome: string) {
  return ({ success: "返回有效方案", running: "请求中", failed: "调用失败", context_limit: "上下文超限", output_limit: "输出达到上限", invalid_output: "结果校验未通过" } as Record<string, string>)[outcome] ?? outcome;
}
export function maintenanceInputLimit(config: Pick<Config, "maintenance_input_token_budget" | "maintenance_safety_tokens">, model: Pick<ModelConfig, "context_window_tokens" | "maintenance_output_tokens">) {
  const desired = config.maintenance_input_token_budget;
  return model.context_window_tokens == null ? desired : Math.min(desired, model.context_window_tokens - (model.maintenance_output_tokens ?? 8192) - config.maintenance_safety_tokens);
}

export function maintenanceStatusLabel(job: Pick<Job, "status" | "error" | "deferred_messages" | "waiting_for_context">) {
  if (job.status === "completed" && job.waiting_for_context) return "进度已保存，尾部等待补充";
  if (job.status === "deferred") {
    if (/本轮.*(?:调用|批次|批整理).*上限/.test(job.error ?? "")) return "已达本轮上限，整理暂停";
    if (job.deferred_messages?.length) return "材料待补充，部分暂缓";
    return "整理已暂停";
  }
  return ({ queued: "排队中", running: "正在整理", completed: "已完成", failed: "失败", skipped: "已跳过" } as Record<string, string>)[job.status] ?? job.status;
}
