import test from "node:test";
import assert from "node:assert/strict";
import {
  latency,
  modeName,
} from "../.test-build/retrievalUi.js";
import { histogram, metric } from "../.test-build/monitoringMetrics.js";

test("mode labels and latency distinguish program and embedding recall", () => {
  assert.equal(modeName("auto"), "程序召回");
  assert.equal(modeName("local"), "程序召回");
  assert.equal(modeName("hybrid"), "语义增强召回");
  assert.equal(latency(8400), "8.40 秒");
  assert.equal(latency(12.345), "12.3 ms");
});
test("route and stage histograms can be inspected without mixing purposes", () => {
  const snapshot = {
    metrics: [
      {
        samples: [
          {
            name: "memory_retrieval_route_duration_seconds_count",
            labels: { route: "embedding" },
            value: 2,
          },
          {
            name: "memory_retrieval_route_duration_seconds_sum",
            labels: { route: "embedding" },
            value: 4,
          },
          {
            name: "memory_retrieval_route_duration_seconds_count",
            labels: { route: "lexical" },
            value: 10,
          },
          {
            name: "memory_retrieval_route_duration_seconds_sum",
            labels: { route: "lexical" },
            value: 0.1,
          },
          {
            name: "memory_model_requests_total",
            labels: { purpose: "query", outcome: "success" },
            value: 2,
          },
          {
            name: "memory_model_requests_total",
            labels: { purpose: "index", outcome: "success" },
            value: 20,
          },
        ],
      },
    ],
  };
  assert.equal(
    histogram(snapshot, "memory_retrieval_route_duration_seconds", {
      route: "embedding",
    }).average,
    2,
  );
  assert.equal(
    histogram(snapshot, "memory_retrieval_route_duration_seconds", {
      route: "lexical",
    }).average,
    0.01,
  );
  assert.equal(
    metric(snapshot, "memory_model_requests_total", { purpose: "query" }),
    2,
  );
});
