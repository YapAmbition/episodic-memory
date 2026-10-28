import test from "node:test";
import assert from "node:assert/strict";
import { maintenanceSummary, maintenanceInputLimit, outcomeName } from "../.test-build/maintenanceUi.js";
import { histogram, metric } from "../.test-build/monitoringMetrics.js";

test("unknown model capacity keeps an explicit conservative input budget", () => {
  assert.equal(maintenanceInputLimit({maintenance_input_token_budget:8000, maintenance_safety_tokens:512}, {}),8000);
});
test("known capacity subtracts output and safety while respecting configured input ceiling", () => {
  assert.equal(maintenanceInputLimit({maintenance_input_token_budget:8000,maintenance_safety_tokens:512},{context_window_tokens:7000,maintenance_output_tokens:2000}),4488);
  assert.equal(maintenanceInputLimit({maintenance_input_token_budget:3000,maintenance_safety_tokens:512},{context_window_tokens:7000,maintenance_output_tokens:2000}),3000);
});
test("job summaries distinguish extra reads, adaptations, and failures", () => {
  assert.equal(maintenanceSummary({model_call_count:4,adaptation_count:1,read_rounds:2}),"4 次模型调用 · 1 次缩批 · 2 次补读");
  assert.equal(outcomeName("context_limit"),"上下文超限");
  assert.equal(outcomeName("output_limit"),"输出达到上限");
  assert.equal(outcomeName("invalid_output"),"结果校验未通过");
});
test("estimated input metrics remain separate from actual provider token usage", () => {
  const snapshot={metrics:[{samples:[{name:"memory_maintenance_estimated_input_tokens_count",labels:{source:"fallback"},value:2},{name:"memory_maintenance_estimated_input_tokens_sum",labels:{source:"fallback"},value:6000},{name:"memory_model_tokens_total",labels:{purpose:"maintenance",direction:"input"},value:1800}]}]};
  assert.equal(histogram(snapshot,"memory_maintenance_estimated_input_tokens").average,3000);
  assert.equal(metric(snapshot,"memory_model_tokens_total",{purpose:"maintenance",direction:"input"}),1800);
});
