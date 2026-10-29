# 0.7.0 后端功能与接口

HTTP 服务是公共记忆核心的适配层，完整入参和响应约束以运行实例 `/docs` 和 `/openapi.json` 为准。核心可直接嵌入 Python 应用，见 [核心架构](core-architecture.md)。

业务前缀 `/api/v1`，配置 `MEMORY_API_TOKEN` 后携带 `Authorization: Bearer <token>`；当前令牌不是空间级授权。

| 功能 | 路径（省略业务前缀） |
| --- | --- |
| 空间列表 / 创建 | `GET /spaces`、`POST /spaces` |
| 人物列表 / 创建 / 更新 | `GET /spaces/{sid}/persons`、`POST /spaces/{sid}/persons`、`PUT /spaces/{sid}/persons/{pid}` |
| 记忆配置 | `GET /spaces/{sid}/config`、`PUT /spaces/{sid}/config` |
| 模型配置与测试 | `GET /spaces/{sid}/model`、`PUT /spaces/{sid}/model`、`POST /spaces/{sid}/model/test` |
| 当前能力 | `GET /spaces/{sid}/capabilities` |
| 会话列表 / 创建 | `GET /spaces/{sid}/conversations`、`POST /spaces/{sid}/conversations` |
| 原始消息列表 / 写入 | `GET /spaces/{sid}/conversations/{cid}/messages`、`POST /spaces/{sid}/conversations/{cid}/messages` |
| 内置聊天模拟 | `POST /spaces/{sid}/conversations/{cid}/chat` |
| 事件列表 / 创建 | `GET /spaces/{sid}/events`、`POST /spaces/{sid}/events` |
| 事件读取 / 修改 / 删除 | `GET /spaces/{sid}/events/{eid}`、`PUT /spaces/{sid}/events/{eid}`、`DELETE /spaces/{sid}/events/{eid}` |
| 合并 / 拆分 / 强化 | `POST /spaces/{sid}/events/merge`、`POST /spaces/{sid}/events/{eid}/split`、`POST /spaces/{sid}/events/{eid}/reinforce` |
| 来源片段 / 历史版本 | `GET /spaces/{sid}/events/{eid}/fragments`、`GET /spaces/{sid}/events/{eid}/versions` |
| 维护操作记录 | `GET /spaces/{sid}/operations` |
| 上下文 / 候选搜索 | `POST /spaces/{sid}/context`、`POST /spaces/{sid}/search` |
| 召回记录 / 详情 / 反馈 | `GET /spaces/{sid}/recalls`、`GET /spaces/{sid}/recalls/{rid}`、`PUT /spaces/{sid}/recalls/{rid}/feedback` |
| 固定案例评测 | `POST /spaces/{sid}/evaluations` |
| 整理 / 任务列表 / 详情 / 重试 | `POST /spaces/{sid}/maintenance`、`GET /spaces/{sid}/jobs`、`GET /spaces/{sid}/jobs/{job_id}`、`POST /spaces/{sid}/jobs/{job_id}/retry` |
| 空间统计 / 导出 | `GET /spaces/{sid}/stats`、`GET /spaces/{sid}/export` |
| 全服务监控 | `GET /monitoring` |

## 召回

```json
{"query":"这次面包又失败了","retrieval_mode":"local","trace":true}
```

`person_id` 可选，`at` 留空使用当前时间；`recent_messages` 可提供最近上下文。`local` 使用全文、锚点、人物和时间路径；人物加分仅在配置关注对象后启用。`hybrid` 额外检索远程向量，失败自动使用程序结果。`auto` 是 local 的兼容别名；`llm` 与 `use_model=true` 返回 422。

`/context` 返回 `context`、`selected`、`candidates`、`seeds`、`omitted`、`route_status`、`timings_ms` 等。沿关联读取原文，超预算省略整件事；`context_char_budget` 为字符预算，不是 tokenizer 容量。`/search` 只返回限量入口候选，不展开关联或返回拼接正文。召回、阅读和时间模拟均不强化记忆。

远程向量接口（空间路径前缀 `/api/v1/spaces/{sid}`）：

- `GET /embedding`：配置和健康状态，包含 `has_key`、`indexed_chunks`、`pending_chunks`、`last_error`、`circuit_open`、`retry_after_seconds`，不返回密钥。
- `PUT /embedding`：完整配置 `enabled`、`base_url`、`model`、可选 `api_key`、`dimensions`、`timeout_seconds`（默认 1.5）、`failure_threshold`（3）、`cooldown_seconds`（30）、`min_similarity`（0.45）。密钥省略保留、空字符串清除内存值；环境变量仍可供给密钥。
- `POST /embedding/test`：测试已保存连接，返回 `{ok, reason?}`；同样服从超时和熔断。

无需手工启动索引，后台自动追踪片段内容哈希和模型签名。旧 `/embedding/status`、`/embedding/reindex` 不恢复。AstrBot 管理页只允许 GET，参数由宿主配置控制。早期向量数据不复用，原始事件及消息不受影响。

召回结果中的 `route_status.embedding` 表示 `ok` / `partial` / `not_configured` / `index_pending` / `timeout` / `auth` / `rate_limited` / `network` / `http_error` / `invalid_response` / `dimension_changed` / `dimension_mismatch` / `circuit_open` 等状态；`effective_mode` 指实际 local 或 hybrid，`degraded`、`warnings` 标明降级。向量故障不让 context/search/chat 失败。

## 整理与数据一致性

`POST /maintenance` 返回 202 和任务 ID。任务可新建多件事、追加旧事、忽略或暂缓，不保证每次新增一件。每批结果与消息处理进度原子提交；解析、引用或证据校验失败时保留原消息及未完成进度。任务详情提供安全诊断、输入预算、缩批和补读记录。

模型连接支持上下文容量、输出预留；未知容量使用显式输入预算。超限有限缩批，超长消息按源位置分段，旧事件按预算补读。候选不等于全库，可能漏关联；参见 [记忆机制](memory-mechanisms.md)。

事件修改带 `expected_version` 可检查并发冲突；重复 `client_id` 的正文、作者、回复和提及必须一致。显式强化必须引用真实用户消息。删除事件会清理事件版本与相关引用、历史召回，但保留原始聊天，不等于彻底遗忘。

## 群聊与关注对象

每条群消息独立提交 `sender_id`、`in_reply_to`、`mention_ids`；平台 UID 先注册映射为本服务人物 ID。关注配置、缓冲期限、原话证据、待处理消息及长期来源规则见 [群聊接入](focused-memory.md)。

## 监控与认证

`/api/health` 不需要认证。`/metrics` 使用 `MEMORY_METRICS_TOKEN`，留空时沿用业务令牌；JSON 监控使用业务令牌。模型密钥不回传，页面填写的密钥只保存在进程内。统计接口不调用模型。见 [监控说明](../monitoring/README.md)。

## 宿主运行模式

`GET /api/v1/runtime` 使用业务令牌，返回 `mode`、`managed_configuration`、`simulator_enabled`。独立服务保持原功能；AstrBot 模式的网页与插件共用核心，空间列表附带 `host` 来源及启用状态。

AstrBot 模式禁止配置、模型、人物、空间、会话、消息及模拟聊天的写入（403）。允许事件编辑、检索、上下文召回、整理、任务重试及召回反馈；停用空间的整理和任务重试返回 409。`/metrics` 与业务接口统一使用插件的 `web_token`。


### 请求上下文与记忆去重

核心 `/context` 输入新增可选 `current_context: string[]`，传入当前模型请求中实际存在的文本块（包括系统提示、历史、本轮输入和额外文本）。不传时保持原有召回行为；不要用永久会话历史代替实际请求。核心扫描全部文本检查完整记忆块，查询线索按近期字符预算选择，并排除协议记忆及规则块，不把全部上下文发送给 Embedding。

事件输出使用 `<memory-slice id="event_123" revision="2" supersedes="1">完整正文</memory-slice>`；revision 复用事件存储版本，修改事件会递增。正文 XML 转义。同 ID 在当前上下文已有同版或更新版本时跳过，旧版则输出最新完整记录，去重原因位于 `omitted`。旧 `<event>` 格式不会误判为新版协议。

规则使用 `<memory-slice-rules version="1">…</memory-slice-rules>`；有记忆输出且当前上下文缺少本版完整规则时附带。上下文裁剪后可再次注入，不维护会话永久已读状态。规则占用上下文字符预算。标签检测不是安全认证，接入方应传递真实请求内容。

AstrBot 插件从 ProviderRequest 传递当前可见文本，仍使用临时注入，不修改历史；后续框架裁剪或其他插件修改不在此快照内。状态命令为 `/记忆切片`，召回仅支持 local/hybrid；hybrid 可能增加聊天延迟，失败自动降级。
