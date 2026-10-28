import type { MetricSample, MonitoringSnapshot } from "./types";

export type Percentile = { value: number; lowerBound: boolean };
export type Histogram = {
  count: number;
  sum: number;
  average: number | null;
  p95: Percentile | null;
};
export type Observation = {
  at: number;
  startedAt: number;
  uptime: number;
  http: number;
  recalls: number;
  modelAttempts: number;
  requestsPerSecond: number | null;
  recallsPerSecond: number | null;
};
export type RouteMetric = {
  method: string;
  route: string;
  total: number;
  clientErrors: number;
  serverErrors: number;
  latency: Histogram;
};
export type ModelMetric = {
  purpose: string;
  total: number;
  success: number;
  attempts: number;
  input: number;
  output: number;
  hasUsage: boolean;
  inputReported: boolean;
  outputReported: boolean;
  missingUsage: number;
  latency: Histogram;
};

export function samples(
  snapshot: MonitoringSnapshot,
  name: string,
  labels: Record<string, string> = {},
): MetricSample[] {
  return snapshot.metrics
    .flatMap((family) => family.samples)
    .filter(
      (sample) =>
        sample.name === name &&
        Number.isFinite(sample.value) &&
        Object.entries(labels).every(
          ([key, value]) => sample.labels[key] === value,
        ),
    );
}

export function metric(
  snapshot: MonitoringSnapshot,
  name: string,
  labels: Record<string, string> = {},
): number {
  return samples(snapshot, name, labels).reduce(
    (sum, sample) => sum + sample.value,
    0,
  );
}

export function histogram(
  snapshot: MonitoringSnapshot,
  name: string,
  labels: Record<string, string> = {},
): Histogram {
  const count = metric(snapshot, name + "_count", labels);
  const sum = metric(snapshot, name + "_sum", labels);
  const buckets = new Map<number, number>();
  for (const sample of samples(snapshot, name + "_bucket", labels)) {
    const upper =
      sample.labels.le === "+Inf" ? Infinity : Number(sample.labels.le);
    if (!Number.isNaN(upper))
      buckets.set(upper, (buckets.get(upper) ?? 0) + sample.value);
  }
  let p95: Percentile | null = null;
  if (count > 0 && buckets.size) {
    const rank = 0.95 * count;
    let previousUpper = 0,
      previousCount = 0;
    for (const [upper, cumulative] of [...buckets].sort(
      (a, b) => a[0] - b[0],
    )) {
      if (cumulative >= rank) {
        if (Number.isFinite(upper)) {
          const fraction =
            cumulative > previousCount
              ? (rank - previousCount) / (cumulative - previousCount)
              : 0;
          p95 = {
            value: previousUpper + fraction * (upper - previousUpper),
            lowerBound: false,
          };
        } else {
          p95 = { value: previousUpper, lowerBound: true };
        }
        break;
      }
      previousUpper = upper;
      previousCount = cumulative;
    }
  }
  return { count, sum, average: count > 0 ? sum / count : null, p95 };
}

export function httpSummary(snapshot: MonitoringSnapshot) {
  const values = samples(snapshot, "memory_http_requests_total");
  const total = values.reduce((sum, sample) => sum + sample.value, 0);
  const statusCount = (prefix: string) =>
    values
      .filter((sample) => sample.labels.status?.startsWith(prefix))
      .reduce((sum, sample) => sum + sample.value, 0);
  const serverErrors = statusCount("5"),
    clientErrors = statusCount("4");
  return {
    total,
    serverErrors,
    clientErrors,
    serverErrorRate: total > 0 ? serverErrors / total : null,
    latency: histogram(snapshot, "memory_http_request_duration_seconds"),
  };
}

export function httpRoutes(snapshot: MonitoringSnapshot): RouteMetric[] {
  const groups = new Map<string, RouteMetric>();
  for (const sample of samples(snapshot, "memory_http_requests_total")) {
    const { method, route, status } = sample.labels;
    const key = method + " " + route;
    const value = groups.get(key) ?? {
      method,
      route,
      total: 0,
      clientErrors: 0,
      serverErrors: 0,
      latency: histogram(snapshot, "memory_http_request_duration_seconds", {
        method,
        route,
      }),
    };
    value.total += sample.value;
    if (status.startsWith("4")) value.clientErrors += sample.value;
    if (status.startsWith("5")) value.serverErrors += sample.value;
    groups.set(key, value);
  }
  return [...groups.values()].sort(
    (a, b) => b.total - a.total || a.route.localeCompare(b.route),
  );
}

export function modelMetrics(snapshot: MonitoringSnapshot): ModelMetric[] {
  const purposes = new Set(
    samples(snapshot, "memory_model_requests_total").map(
      (sample) => sample.labels.purpose,
    ),
  );
  return [...purposes]
    .map((purpose) => {
      const labels = { purpose };
      return {
        purpose,
        total: metric(snapshot, "memory_model_requests_total", labels),
        success: metric(snapshot, "memory_model_requests_total", {
          purpose,
          outcome: "success",
        }),
        attempts: metric(snapshot, "memory_model_attempts_total", labels),
        input: metric(snapshot, "memory_model_tokens_total", {
          purpose,
          direction: "input",
        }),
        output: metric(snapshot, "memory_model_tokens_total", {
          purpose,
          direction: "output",
        }),
        hasUsage:
          samples(snapshot, "memory_model_tokens_total", labels).length > 0,
        inputReported:
          samples(snapshot, "memory_model_tokens_total", {
            purpose,
            direction: "input",
          }).length > 0,
        outputReported:
          samples(snapshot, "memory_model_tokens_total", {
            purpose,
            direction: "output",
          }).length > 0,
        missingUsage: metric(
          snapshot,
          "memory_model_usage_missing_total",
          labels,
        ),
        latency: histogram(
          snapshot,
          "memory_model_request_duration_seconds",
          labels,
        ),
      };
    })
    .sort((a, b) => b.total - a.total || a.purpose.localeCompare(b.purpose));
}

export function observe(
  history: Observation[],
  snapshot: MonitoringSnapshot,
  at = Date.now(),
): { history: Observation[]; reset: boolean } {
  const current: Observation = {
    at,
    startedAt: snapshot.started_at_unix,
    uptime: snapshot.uptime_seconds,
    http: metric(snapshot, "memory_http_requests_total"),
    recalls: metric(snapshot, "memory_recalls_total"),
    modelAttempts: metric(snapshot, "memory_model_attempts_total"),
    requestsPerSecond: null,
    recallsPerSecond: null,
  };
  const previous = history.at(-1);
  const reset =
    !!previous &&
    (previous.startedAt !== current.startedAt ||
      current.http < previous.http ||
      current.recalls < previous.recalls ||
      current.modelAttempts < previous.modelAttempts);
  if (reset || !previous) return { history: [current], reset };
  const elapsed = current.uptime - previous.uptime;
  if (elapsed <= 0) return { history, reset: false };
  // A long disconnect is a gap, not a line interpolated across missing samples.
  if (elapsed <= 90) {
    current.requestsPerSecond = (current.http - previous.http) / elapsed;
    current.recallsPerSecond = (current.recalls - previous.recalls) / elapsed;
  }
  return {
    history: [
      ...history.filter((point) => at - point.at <= 300_000),
      current,
    ].slice(-80),
    reset: false,
  };
}

export function assertSnapshot(value: MonitoringSnapshot): void {
  if (
    !value ||
    !Array.isArray(value.metrics) ||
    !value.storage ||
    !Number.isFinite(value.started_at_unix) ||
    !Number.isFinite(value.uptime_seconds)
  ) {
    throw new Error(
      "服务未返回可识别的指标，请确认启动的是包含监控接口的新版后端。",
    );
  }
}

export function count(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(
    value,
  );
}
export function rate(value: number | null): string {
  return value === null ? "—" : (value * 100).toFixed(1) + "%";
}
export function duration(value: number | null): string {
  if (value === null) return "—";
  if (value < 0.001) return "<1 ms";
  return value < 1
    ? Math.round(value * 1000) + " ms"
    : value.toFixed(value < 10 ? 2 : 1) + " s";
}
export function percentile(value: Percentile | null): string {
  return value ? (value.lowerBound ? "≥ " : "≈ ") + duration(value.value) : "—";
}
export function bytes(value: number | null): string {
  if (value === null) return "未提供";
  const units = ["B", "KB", "MB", "GB"];
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index++;
  }
  return value.toFixed(index ? 1 : 0) + " " + units[index];
}
export function uptime(value: number): string {
  if (value < 60) return Math.floor(value) + " 秒";
  if (value < 3600)
    return Math.floor(value / 60) + " 分 " + Math.floor(value % 60) + " 秒";
  if (value < 86400)
    return (
      Math.floor(value / 3600) +
      " 小时 " +
      Math.floor((value % 3600) / 60) +
      " 分"
    );
  return (
    Math.floor(value / 86400) +
    " 天 " +
    Math.floor((value % 86400) / 3600) +
    " 小时"
  );
}
