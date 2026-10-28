import test from "node:test";
import assert from "node:assert/strict";
import {
  assertSnapshot,
  bytes,
  duration,
  histogram,
  httpRoutes,
  httpSummary,
  metric,
  modelMetrics,
  observe,
  percentile,
} from "../.test-build/monitoringMetrics.js";

function snapshot(samples = [], overrides = {}) {
  return {
    scope: "current_process",
    started_at_unix: 1000,
    uptime_seconds: 10,
    storage: {
      records: {},
      jobs: {},
      pending_messages: 0,
      database_bytes: 1000,
    },
    metrics: [{ name: "test", type: "untyped", help: "", samples }],
    notes: [],
    ...overrides,
  };
}
const sample = (name, value, labels = {}) => ({ name, value, labels });

function traffic(http, recalls, uptime, started = 1000) {
  return snapshot(
    [
      sample("memory_http_requests_total", http, {
        method: "GET",
        route: "/api/test",
        status: "200",
      }),
      sample("memory_recalls_total", recalls, { outcome: "success" }),
    ],
    { uptime_seconds: uptime, started_at_unix: started },
  );
}

test("empty histograms and absent process metrics remain unknown", () => {
  const data = snapshot();
  assert.deepEqual(histogram(data, "latency"), {
    count: 0,
    sum: 0,
    average: null,
    p95: null,
  });
  assert.equal(duration(null), "—");
  assert.equal(bytes(null), "未提供");
  assert.equal(httpSummary(data).serverErrorRate, null);
  assert.equal(modelMetrics(data).length, 0);
});

test("P95 is interpolated from histogram buckets, not replaced by the mean", () => {
  const data = snapshot([
    sample("latency_count", 100),
    sample("latency_sum", 200),
    sample("latency_bucket", 80, { le: "1" }),
    sample("latency_bucket", 100, { le: "5" }),
    sample("latency_bucket", 100, { le: "+Inf" }),
  ]);
  const result = histogram(data, "latency");
  assert.equal(result.average, 2);
  assert.deepEqual(result.p95, { value: 4, lowerBound: false });
});

test("P95 aggregates bucket counts across purposes before calculating the percentile", () => {
  const data = snapshot([
    sample("latency_count", 10, { purpose: "a" }),
    sample("latency_sum", 5, { purpose: "a" }),
    sample("latency_bucket", 10, { le: "1", purpose: "a" }),
    sample("latency_bucket", 10, { le: "5", purpose: "a" }),
    sample("latency_bucket", 10, { le: "+Inf", purpose: "a" }),
    sample("latency_count", 90, { purpose: "b" }),
    sample("latency_sum", 300, { purpose: "b" }),
    sample("latency_bucket", 0, { le: "1", purpose: "b" }),
    sample("latency_bucket", 90, { le: "5", purpose: "b" }),
    sample("latency_bucket", 90, { le: "+Inf", purpose: "b" }),
  ]);
  assert.equal(histogram(data, "latency").count, 100);
  assert.ok(
    Math.abs(histogram(data, "latency").p95.value - (1 + (85 / 90) * 4)) <
      1e-10,
  );
  assert.deepEqual(histogram(data, "latency", { purpose: "a" }).p95, {
    value: 0.95,
    lowerBound: false,
  });
});

test("overflow bucket is displayed as a lower bound rather than a precise percentile", () => {
  const data = snapshot([
    sample("latency_count", 100),
    sample("latency_sum", 10000),
    sample("latency_bucket", 5, { le: "60" }),
    sample("latency_bucket", 100, { le: "+Inf" }),
  ]);
  assert.deepEqual(histogram(data, "latency").p95, {
    value: 60,
    lowerBound: true,
  });
  assert.equal(percentile(histogram(data, "latency").p95), "≥ 60.0 s");
});

test("request status classes and route methods are not mixed", () => {
  const data = snapshot([
    sample("memory_http_requests_total", 7, {
      method: "GET",
      route: "/api/x",
      status: "200",
    }),
    sample("memory_http_requests_total", 2, {
      method: "GET",
      route: "/api/x",
      status: "401",
    }),
    sample("memory_http_requests_total", 1, {
      method: "POST",
      route: "/api/x",
      status: "503",
    }),
  ]);
  assert.equal(httpSummary(data).total, 10);
  assert.equal(httpSummary(data).serverErrors, 1);
  assert.equal(httpSummary(data).clientErrors, 2);
  assert.equal(httpSummary(data).serverErrorRate, 0.1);
  const routes = httpRoutes(data);
  assert.equal(routes.length, 2);
  assert.equal(routes[0].method, "GET");
  assert.equal(routes[0].total, 9);
  assert.equal(routes[0].clientErrors, 2);
  assert.equal(routes[1].serverErrors, 1);
});

test("missing provider usage is distinct from a reported zero token count", () => {
  const missing = snapshot([
    sample("memory_model_requests_total", 1, {
      purpose: "chat",
      outcome: "success",
    }),
    sample("memory_model_usage_missing_total", 1, { purpose: "chat" }),
  ]);
  assert.equal(modelMetrics(missing)[0].hasUsage, false);
  assert.equal(modelMetrics(missing)[0].missingUsage, 1);
  const zero = snapshot([
    ...missing.metrics[0].samples,
    sample("memory_model_tokens_total", 0, {
      purpose: "chat",
      direction: "input",
    }),
  ]);
  assert.equal(modelMetrics(zero)[0].hasUsage, true);
  assert.equal(modelMetrics(zero)[0].input, 0);
  assert.equal(modelMetrics(zero)[0].inputReported, true);
  assert.equal(modelMetrics(zero)[0].outputReported, false);
});

test("rates use actual server uptime differences and need a baseline sample", () => {
  const first = observe([], traffic(5, 2, 10), 10000);
  assert.equal(first.history[0].requestsPerSecond, null);
  const next = observe(first.history, traffic(15, 7, 15), 15000);
  assert.equal(next.history[1].requestsPerSecond, 2);
  assert.equal(next.history[1].recallsPerSecond, 1);
  assert.equal(next.reset, false);
});

test("process restarts and counter decreases clear previous trends", () => {
  const first = observe([], traffic(100, 20, 100), 10000);
  const restarted = observe(first.history, traffic(1, 0, 1, 2000), 11000);
  assert.equal(restarted.reset, true);
  assert.equal(restarted.history.length, 1);
  assert.equal(restarted.history[0].requestsPerSecond, null);
  const decreased = observe(first.history, traffic(1, 0, 101), 11000);
  assert.equal(decreased.reset, true);
});

test("long gaps are not interpolated and later sampling resumes", () => {
  const first = observe([], traffic(5, 2, 10), 10000);
  const gap = observe(first.history, traffic(100, 20, 110), 110000);
  assert.equal(gap.history[1].requestsPerSecond, null);
  const next = observe(gap.history, traffic(105, 25, 115), 115000);
  assert.equal(next.history[2].requestsPerSecond, 1);
});

test("history is bounded and old observations expire", () => {
  let history = [];
  for (let i = 0; i < 150; i++)
    history = observe(history, traffic(i, i, i + 1), i * 5000).history;
  assert.ok(history.length <= 80);
  assert.ok(history[0].at >= history.at(-1).at - 300000);
});

test("malformed monitoring responses are rejected before rendering", () => {
  assert.throws(() => assertSnapshot({}), /可识别的指标/);
  assert.doesNotThrow(() => assertSnapshot(snapshot()));
  assert.equal(metric(snapshot([sample("x", NaN), sample("x", 5)]), "x"), 5);
});
