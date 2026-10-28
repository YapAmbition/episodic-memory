# 运行监控

0.6.0 的「运行监控」展示整个服务的请求、模型、召回、整理、关注筛选、存储和进程状态；切换空间不改变范围。公共核心也可通过 `service.metrics.snapshot()` / `.prometheus()` 直接读取。

## 接口

- `GET /api/health`：健康和版本，无需认证。
- `GET /api/v1/monitoring`：JSON 快照，使用业务 API 令牌。
- `GET /metrics`：Prometheus 文本，使用 `MEMORY_METRICS_TOKEN`；未设置时沿用 `MEMORY_API_TOKEN`。

抓取示例见 [prometheus.example.yml](prometheus.example.yml)，按实际网络地址配置目标和令牌。计数器及直方图随进程重启归零；数据库记录、任务状态和待整理消息数量持久化。长期趋势由外部 Prometheus 保存。

## 指标

- HTTP：`memory_http_requests_total`、`memory_http_request_duration_seconds`、`memory_http_requests_in_progress`。
- 模型：`memory_model_requests_total`、`memory_model_attempts_total`、`memory_model_request_duration_seconds`、`memory_model_tokens_total`、`memory_model_usage_missing_total`、`memory_model_queue_wait_seconds`。
- 召回：`memory_recalls_total`、`memory_recall_duration_seconds`、`memory_recall_mode_duration_seconds`、`memory_recall_events`、`memory_context_characters`，以及 `memory_retrieval_stage_duration_seconds`、`memory_retrieval_route_duration_seconds`、`memory_retrieval_route_candidates`、`memory_retrieval_route_contributions`。
- 整理：`memory_maintenance_jobs_total`、`memory_maintenance_duration_seconds`、`memory_maintenance_queue_wait_seconds`、`memory_maintenance_stage_duration_seconds`、`memory_maintenance_estimated_input_tokens`、`memory_maintenance_adaptations_total`、`memory_maintenance_candidate_omissions_total`、`memory_maintenance_candidates`、`memory_maintenance_revived_candidates_total`、`memory_maintenance_committed_fragments_total`、`memory_maintenance_deferred_messages_total`。
- 关注：`memory_focus_messages_total{outcome}`，包括 `scanned`、`candidates`、`retained`、`ignored`、`deferred`、`expired`；按处理次数累计，不是去重人数或唯一消息数。
- 其他：`memory_processed_messages_total`、`memory_reinforcements_total`、`memory_recall_feedback_total`、`memory_scheduler_errors_total`、`memory_records`、`memory_maintenance_tasks`、`memory_pending_messages`、`memory_database_bytes`、`memory_uptime_seconds`。

embedding 指标、索引状态和前端卡片已移除。不要将旧版本的语义召回仪表盘直接作为本版状态。

## 解释规则

P95 根据桶估算，溢出桶显示下界；无样本显示未知。模型逻辑调用与 HTTP 尝试分别计数，只有服务商实际上报的 token 才计入用量。整理输入 token 是保守估算，与实际用量分开。

不同路径可命中同一入口，不能相加作为唯一事件总数。阶段可能包含子步骤，耗时不能简单相加。前端速率是相邻有效采样计数差除以时间间隔；隐藏页面暂停采样，断线及进程重启后重新建立基线。

标签不含用户内容、人物 UID、消息 ID、模型地址或密钥。核心错误日志使用 `memory_slice` logger；整理失败记录仅包含阶段、字段约束和安全堆栈位置，不输出原始模型回复或密钥。
