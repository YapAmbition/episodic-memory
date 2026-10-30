"""Per-process metrics. Labels never contain conversation content or record IDs."""
import asyncio
import os
import time

from prometheus_client import CONTENT_TYPE_LATEST as CONTENT_TYPE_LATEST

from prometheus_client import (
    CollectorRegistry, Counter, Gauge, Histogram, ProcessCollector,
    PlatformCollector, generate_latest,
)


class Monitoring:
    def __init__(self, store):
        self.store = store
        self.started = time.time()
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        self.http = Counter("memory_http_requests_total", "Completed HTTP requests", ["method", "route", "status"], registry=self.registry)
        self.http_seconds = Histogram("memory_http_request_duration_seconds", "HTTP request duration", ["method", "route"], buckets=(.01, .05, .1, .5, 1, 5, 15, 30, 60, 120, 300), registry=self.registry)
        self.inflight = Gauge("memory_http_requests_in_progress", "In-progress HTTP requests", registry=self.registry)
        self.model_calls = Counter("memory_model_requests_total", "Logical model calls", ["purpose", "outcome"], registry=self.registry)
        self.model_attempts = Counter("memory_model_attempts_total", "Model HTTP attempts", ["purpose", "outcome"], registry=self.registry)
        self.model_seconds = Histogram("memory_model_request_duration_seconds", "Model duration including retries and queue wait", ["purpose"], buckets=(.1, .5, 1, 3, 10, 30, 60, 120, 300), registry=self.registry)
        self.tokens = Counter("memory_model_tokens_total", "Provider-reported tokens only", ["purpose", "direction"], registry=self.registry)
        self.usage_missing = Counter("memory_model_usage_missing_total", "Successful calls without token usage", ["purpose"], registry=self.registry)
        self._model_slots = None
        self.recalls = Counter("memory_recalls_total", "Recall outcomes", ["strategy", "outcome"], registry=self.registry)
        self.recall_seconds = Histogram("memory_recall_duration_seconds", "End-to-end recall duration including models", buckets=(.01, .05, .1, .5, 1, 5, 15, 30, 60, 120, 300), registry=self.registry)
        self.recall_mode_seconds = Histogram("memory_recall_mode_duration_seconds", "Recall duration by executed mode (includes failures and cancellation)", ["mode"], buckets=(.001, .005, .01, .05, .1, .5, 1, 3, 10, 30, 120, 300), registry=self.registry)
        self.recall_events = Histogram("memory_recall_events", "Number of retrieved events", ["stage"], buckets=(0, 1, 3, 5, 10, 20, 40, 80, 160), registry=self.registry)
        self.context_chars = Histogram("memory_context_characters", "Injected context characters", buckets=(0, 1000, 4000, 10000, 18000, 40000, 100000), registry=self.registry)
        timing_buckets = (.001, .005, .01, .05, .1, .5, 1, 3, 10, 30, 120)
        self.retrieval_stage = Histogram("memory_retrieval_stage_duration_seconds", "Recall stage wall time (parallel stages may overlap)", ["stage"], buckets=timing_buckets, registry=self.registry)
        self.route_seconds = Histogram("memory_retrieval_route_duration_seconds", "Retrieval route duration", ["route"], buckets=timing_buckets, registry=self.registry)
        self.route_candidates = Histogram("memory_retrieval_route_candidates", "Candidates per route", ["route"], buckets=(0, 1, 3, 5, 10, 20, 40, 120), registry=self.registry)
        self.route_contributions = Histogram("memory_retrieval_route_contributions", "Selected entrances matching each route (overlap allowed)", ["route"], buckets=(0, 1, 3, 5, 12), registry=self.registry)
        self.embedding_provider = lambda: []
        self.embedding_health = Gauge("memory_embedding_index_chunks", "Embedding chunks by state", ["state"], registry=self.registry)
        self.embedding_circuits = Gauge("memory_embedding_open_circuits", "Spaces with open embedding circuit", registry=self.registry)
        self.embedding_calls = Counter("memory_embedding_requests_total", "Remote embedding calls", ["purpose", "outcome"], registry=self.registry)
        self.embedding_seconds = Histogram("memory_embedding_duration_seconds", "Embedding time including queue", ["purpose"], buckets=timing_buckets, registry=self.registry)
        self.degradations = Counter("memory_retrieval_degradations_total", "Recall dependency degradations", ["reason"], registry=self.registry)
        self.model_queue_seconds = Histogram("memory_model_queue_wait_seconds", "Model concurrency slot wait", ["purpose"], buckets=timing_buckets, registry=self.registry)
        self.jobs = Counter("memory_maintenance_jobs_total", "Maintenance task outcomes", ["outcome", "trigger"], registry=self.registry)
        self.job_seconds = Histogram("memory_maintenance_duration_seconds", "Active maintenance execution duration", buckets=(.1, 1, 5, 15, 30, 60, 120, 300, 600), registry=self.registry)
        self.job_wait = Histogram("memory_maintenance_queue_wait_seconds", "Time between enqueue and execution", buckets=(.01, 1, 5, 15, 30, 60, 300, 3600), registry=self.registry)
        self.maintenance_stage = Histogram("memory_maintenance_stage_duration_seconds", "Maintenance phase durations", ["stage"], buckets=(.001, .01, .1, 1, 5, 15, 30, 60, 120, 300), registry=self.registry)
        self.maintenance_paused = Gauge("memory_maintenance_paused_spaces", "Spaces with automatic maintenance paused by safety guard", registry=self.registry)
        self.maintenance_input = Histogram("memory_maintenance_estimated_input_tokens", "Estimated input tokens per maintenance model call; NOT provider usage", ["source"], buckets=(512, 1024, 2048, 4096, 8000, 16000, 32000, 64000, 128000, 1000000), registry=self.registry)
        self.maintenance_adaptations = Counter("memory_maintenance_adaptations_total", "Automatic maintenance batch reductions", ["reason"], registry=self.registry)
        self.maintenance_omissions = Counter("memory_maintenance_candidate_omissions_total", "Candidate omissions per final batch", ["reason"], registry=self.registry)
        self.maintenance_candidates = Histogram("memory_maintenance_candidates", "Final batch candidates", ["stage"], buckets=(0, 1, 3, 5, 10, 20, 40, 120), registry=self.registry)
        self.maintenance_revived = Counter("memory_maintenance_revived_candidates_total", "Faded candidates admitted by strong cues; does not reinforce", registry=self.registry)
        self.maintenance_fragments = Counter("memory_maintenance_committed_fragments_total", "Committed contiguous message fragments", registry=self.registry)
        self.maintenance_decisions = Counter("memory_maintenance_decisions_total", "Validated organization decisions", ["mode"], registry=self.registry)
        self.maintenance_deferred = Counter("memory_maintenance_deferred_messages_total", "Messages explicitly deferred for insufficient material", registry=self.registry)
        self.focus_messages = Counter("memory_focus_messages_total", "Focused maintenance and buffer outcomes; scans may repeat", ["outcome"], registry=self.registry)
        self.processed = Counter("memory_processed_messages_total", "Messages committed by maintenance", registry=self.registry)
        self.reinforcements = Counter("memory_reinforcements_total", "Validated reinforcement writes", ["origin"], registry=self.registry)
        self.feedback = Counter("memory_recall_feedback_total", "Human-labelled recalled or missed events", ["verdict"], registry=self.registry)
        self.scheduler_errors = Counter("memory_scheduler_errors_total", "Scheduler loop failures", registry=self.registry)
        self.records = Gauge("memory_records", "Persistent records by kind", ["kind"], registry=self.registry)
        self.queue = Gauge("memory_maintenance_tasks", "Persistent maintenance task state", ["state"], registry=self.registry)
        self.pending = Gauge("memory_pending_messages", "Unprocessed non-fixture messages", registry=self.registry)
        self.database_bytes = Gauge("memory_database_bytes", "SQLite database and WAL bytes", registry=self.registry)
        self.uptime = Gauge("memory_uptime_seconds", "Current process uptime", registry=self.registry)

    @property
    def model_slots(self):
        if self._model_slots is None:
            self._model_slots = asyncio.Semaphore(max(1, int(os.environ.get("MODEL_MAX_CONCURRENT", "3"))))
        return self._model_slots

    def refresh(self):
        self.maintenance_paused.set(sum(1 for r in self.store.list("integration") if r["id"] == "maintenance-safety" and r.get("blocked")))
        snapshot = self.store.operational_snapshot()
        self.embedding_status = self.embedding_provider()
        for state in ("indexed", "pending"):
            self.embedding_health.labels(state).set(sum(s[state + "_chunks"] for s in self.embedding_status if s["enabled"]))
        self.embedding_circuits.set(sum(s["circuit_open"] for s in self.embedding_status))
        self.records.clear()
        for kind, count in snapshot["records"].items():
            self.records.labels(kind=kind).set(count)
        for state in ("queued", "running", "completed", "failed", "skipped", "deferred"):
            self.queue.labels(state=state).set(snapshot["jobs"].get(state, 0))
        self.pending.set(snapshot["pending_messages"])
        self.database_bytes.set(snapshot["database_bytes"])
        self.uptime.set(time.time() - self.started)
        return snapshot

    def prometheus(self):
        self.refresh()
        return generate_latest(self.registry)

    def snapshot(self):
        storage = self.refresh()
        return {
            "scope": "current_process", "started_at_unix": self.started,
            "uptime_seconds": round(time.time() - self.started, 2),
            "storage": storage,
            "embedding": self.embedding_status,
            "metrics": [
                {"name": family.name, "type": family.type, "help": family.documentation,
                 "samples": [{"name": sample.name, "labels": sample.labels, "value": sample.value}
                             for sample in family.samples if not sample.name.endswith("_created")]}
                for family in self.registry.collect()
            ],
            "notes": ["Counters and histograms reset when the process restarts; scrape with Prometheus for history.",
                      "Token counts are provider-reported; absent usage is counted separately. Monetary cost is not estimated.",
                      "Maintenance input budgets are conservative UTF-8 estimates, separate from provider usage; capacity may be unknown."]
        }
