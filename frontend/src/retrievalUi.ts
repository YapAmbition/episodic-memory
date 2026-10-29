import type { RetrievalMode } from "./types";

export const retrievalModes: {
  id: RetrievalMode;
  name: string;
  note: string;
}[] = [
  {
    id: "local",
    name: "程序召回",
    note: "文字、人物与时间线索，无需模型连接。",
  },
  {
    id: "hybrid",
    name: "语义增强召回",
    note: "远程 embedding 与程序检索并用；不可用时自动降级。",
  },
];
export const routeNames: Record<string, string> = {
  lexical: "文字与锚点",
  full_text: "全文",
  anchors: "锚点",
  context_anchors: "情境锚点",
  person_salience: "人物与鲜明度",
  temporal: "时间线索",
  plan_date: "计划日期",
  embedding: "远程向量",
  graph: "关联展开",
  exploration: "探索候选",
  expanded_structure: "结构扩展",
  expanded_scene: "场景扩展",
  expanded_details: "细节扩展",
  expanded_time: "时间扩展",
};
export const stageNames: Record<string, string> = {
  candidate_retrieval: "候选检索",

  fusion: "融合与选入口",
  graph_expansion: "关联展开与读取",
  context_build: "拼接上下文",
  trace_persistence: "保存诊断",
};
export const routeStates: Record<string, string> = {
  ok: "已执行",
  disabled: "已关闭",
  not_requested: "未启用",
  empty_index: "索引为空",
  error: "调用失败",
  partial: "索引待补全",
  circuit_open: "熔断冷却",
  index_pending: "索引未就绪",
  not_configured: "未配置",
  auth: "认证失败",
  timeout: "请求超时",
  network: "网络错误",
  invalid_response: "响应异常",
  rate_limited: "服务限流",
  http_error: "服务响应错误",
  dimension_mismatch: "向量维度不匹配",
  configuration_changed: "连接配置已变更",
  internal_error: "向量检索异常",
  dimension_changed: "维度改变，重建中",
};
export function modeName(mode?: string): string {
  if (mode === "auto") return "程序召回";
  return retrievalModes.find((item) => item.id === mode)?.name ?? "历史召回";
}
export function latency(milliseconds: number): string {
  return milliseconds >= 1000
    ? `${(milliseconds / 1000).toFixed(2)} 秒`
    : `${milliseconds.toFixed(1)} ms`;
}
