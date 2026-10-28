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
    id: "llm",
    name: "大模型联想",
    note: "显式调用聊天模型判断关联，耗时与用量会增加。",
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
  llm: "大模型联想",
  graph: "关联展开",
  exploration: "探索候选",
  expanded_structure: "结构扩展",
  expanded_scene: "场景扩展",
  expanded_details: "细节扩展",
  expanded_time: "时间扩展",
};
export const stageNames: Record<string, string> = {
  candidate_retrieval: "候选检索",
  query_expansion: "模型扩展",
  llm_association: "模型联想",
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
