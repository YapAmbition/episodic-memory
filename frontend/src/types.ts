export type Person = { id: string; name: string; relation: string; platform?: string; external_uid?: string; aliases?: string[] };
export type Space = { id: string; name: string; description: string; host?: {active: boolean; kind?: string; platform?: string; external_id?: string; label: string} };
export type Conversation = {
  kind?: "direct" | "group";
  id: string;
  person_id: string;
  title: string;
  created_at: string;
  demo?: boolean;
};
export type Message = {
  person_id?: string; sender_id?: string | null; in_reply_to?: string | null; mention_ids?: string[]; retention?: string;
  id: string;
  role: "user" | "assistant";
  content: string;
  at: string;
  example?: boolean;
  response_mode?: string;
  recall_id?: string;
};
export type Link = { target_id: string; relation: string; strength: number };
export type Anchors = {
  time: string[];
  scene: string[];
  details: string[];
  structure: string[];
  triggers: string[];
};
export type MemoryEvent = {
  id: string;
  title: string;
  body: string;
  occurred_at: string | null;
  learned_at: string;
  updated_at: string;
  person_ids: string[];
  anchors: Anchors;
  salience: number;
  salience_reason: string;
  links: Link[];
  source_ids: string[];
  status: string;
  version: number;
  origin?: string;
  reinforcements: { at: string; message_id: string }[];
  memory: {
    strength: number;
    age_days: number;
    half_life_days: number;
    future: boolean;
  };
  memory_status: string;
  curve?: { days: number; strength: number }[];
  sources?: Message[];
  backlinks?: { id: string; title: string }[];
};
export type Candidate = {
  event_id: string;
  title: string;
  score: number;
  strength: number;
  cue_score: number;
  reasons: string[];
  eligible: boolean;
  matches: { kind: string; text: string }[];
  retrieval_routes?: string[];
  fusion_score?: number;
  route_ranks?: Record<string, number>;
  semantic_match?: { score: number; view: string; evidence: string } | null;
};
export type Selected = {
  event_id: string;
  score: number;
  depth: number;
  path: string[];
  relation: string;
  event: MemoryEvent;
  reasons: string[];
};
export type Recall = {
  id: string;
  at: string;
  query: string;
  strategy: string;
  selected: Selected[];
  seeds: string[];
  candidates: Candidate[];
  context: string;
  context_chars: number;
  elapsed_ms: number;
  requested_mode?: RetrievalMode;
  effective_mode?: string;
  degraded?: boolean;
  warnings?: { code: string; message: string }[];
  timings_ms?: Record<string, number>;
  route_status?: Record<
    string,
    { status: string; candidate_count: number; elapsed_ms?: number }
  >;
  omitted: { event_id: string; title: string; reason: string }[];
};
export type Job = {
  focus_summary?: {enabled: boolean; scanned?: number; candidates?: number; waiting?: number; submitted?: number; retained?: number; ignored?: number; deferred?: number};
  id: string;
  status: string;
  trigger: string;
  created_at: string;
  finished_at?: string;
  method?: string;
  message_count: number;
  event_ids: string[];
  budget?: MaintenanceBudget;
  candidates?: { event_id: string; title: string; strength: number; cue: number; score: number; revived: boolean; included: boolean }[];
  model_call_count?: number;
  adaptation_count?: number;
  read_rounds?: number;
  fragment_count?: number;
  fragments?: { message_id: string; start: number; end: number; total: number }[];
  attempts?: { estimated_input_tokens: number; input_limit: number; outcome: string; elapsed_ms?: number }[];
  deferred_messages?: { message_id: string; reason: string }[];
  parent_job_id?: string;
  continuation_job_id?: string;
  error?: string;
  error_detail?: { stage: string; code: string; exception_type: string; status_code?: number; error_count?: number; issues?: {field: string; code: string; message: string}[] };
  type?: "maintenance";
  total_count?: number;
  success_count?: number;
  failed_count?: number;
  skipped_count?: number;
};
export type MaintenanceBudget = {
  source: "configured" | "fallback";
  counter: string;
  estimated: boolean;
  context_window_tokens: number | null;
  input_limit: number;
  output_reserved: number;
  safety_reserved: number;
  estimated_input_tokens: number;
  candidate_count: number;
  included_count: number;
  omitted_count: number;
  faded_count: number;
  unrelated_count: number;
  budget_omitted_count: number;
  limit_omitted_count: number;
  revived_count: number;
};
export type Stats = {
  events: number;
  active: number;
  dormant: number;
  links: number;
  pending_messages: number;
  last_job: Job | null;
};
export type Config = {
  focus_person_id: string | null; focus_buffer_hours: number; focus_buffer_limit: number;
  default_retrieval_mode: RetrievalMode;
  retrieval_routes: {
    lexical: boolean;
    person_salience: boolean;
    temporal: boolean;
  };
  half_life_days: number;
  salience_multiplier: number;
  reinforcement_boost: number;
  activation_threshold: number;
  cue_weight: number;
  retention_weight: number;
  person_weight: number;
  propagation_decay: number;
  max_depth: number;
  max_seeds: number;
  max_events: number;
  context_char_budget: number;
  backlinks: boolean;
  retrieval_limit: number;
  graph_candidate_limit: number;
  model_catalog_char_budget: number;
  auto_reinforcement_enabled: boolean;
  maintenance_batch_chars: number;
  maintenance_input_token_budget: number;
  maintenance_safety_tokens: number;
  maintenance_max_adaptations: number;
  maintenance_max_read_rounds: number;
  maintenance_forgetting_weight: number;
  schedule_enabled: boolean;
  schedule_mode: string;
  schedule_interval_minutes: number;
  schedule_daily_time: string;
  timezone: string;
  maintenance_prompt: string;
  simulator_prompt: string;
};
export type RetrievalMode = "local" | "hybrid";
export type Capabilities = {
  default_retrieval_mode: RetrievalMode;
  retrieval_modes: Record<
    RetrievalMode,
    { available: boolean; reason: string }
  >;
};
export type ModelConfig = {
  mode: "local" | "llm";
  base_url: string;
  model: string;
  has_key: boolean;
  key_source: string;
  context_window_tokens?: number | null;
  maintenance_output_tokens?: number;
  timeout_seconds?: number;
  max_retries?: number;
};

export type MetricSample = {
  name: string;
  labels: Record<string, string>;
  value: number;
};
export type MetricFamily = {
  name: string;
  type: string;
  help: string;
  samples: MetricSample[];
};
export type MonitoringSnapshot = {
  embedding?: {space_id: string; name: string; enabled: boolean; indexed_chunks: number; pending_chunks: number; last_error: string | null; circuit_open: boolean; retry_after_seconds: number}[];
  scope: string;
  started_at_unix: number;
  uptime_seconds: number;
  storage: {
    records: Record<string, number>;
    jobs: Record<string, number>;
    pending_messages: number;
    database_bytes: number;
  };
  metrics: MetricFamily[];
  notes: string[];
};
