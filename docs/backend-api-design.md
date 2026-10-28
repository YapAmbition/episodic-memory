# 0.6.0 后端功能与接口

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

`person_id` 可选，`at` 留空使用当前时间；`recent_messages` 可提供最近上下文。`local` 使用全文、锚点、人物和时间路径；人物加分仅在配置关注对象后启用。`llm` 显式调用已配置模型扩展线索及判断关联。`auto` 仅为旧请求兼容别名，不会调用向量服务；`hybrid` 返回 422。

`/context` 返回 `context`、`selected`、`candidates`、`seeds`、`omitted`、`route_status`、`timings_ms` 等。沿关联读取原文，超预算省略整件事；`context_char_budget` 为字符预算，不是 tokenizer 容量。`/search` 只返回限量入口候选，不展开关联或返回拼接正文。召回、阅读和时间模拟均不强化记忆。

旧 `/embedding`、`/embedding/test`、`/embedding/status`、`/embedding/reindex` 接口全部移除，不再提供向量相关能力和指标。旧数据库的事件、来源、版本及关系不因此删除。

## 整理与数据一致性

`POST /maintenance` 返回 202 和任务 ID。任务可新建多件事、追加旧事、忽略或暂缓，不保证每次新增一件。每批结果与消息处理进度原子提交；解析、引用或证据校验失败时保留原消息及未完成进度。任务详情提供安全诊断、输入预算、缩批和补读记录。

模型连接支持上下文容量、输出预留；未知容量使用显式输入预算。超限有限缩批，超长消息按源位置分段，旧事件按预算补读。候选不等于全库，可能漏关联；参见 [记忆机制](memory-mechanisms.md)。

事件修改带 `expected_version` 可检查并发冲突；重复 `client_id` 的正文、作者、回复和提及必须一致。显式强化必须引用真实用户消息。删除事件会清理事件版本与相关引用、历史召回，但保留原始聊天，不等于彻底遗忘。

## 群聊与关注对象

每条群消息独立提交 `sender_id`、`in_reply_to`、`mention_ids`；平台 UID 先注册映射为本服务人物 ID。关注配置、缓冲期限、原话证据、待处理消息及长期来源规则见 [群聊接入](focused-memory.md)。

## 监控与认证

`/api/health` 不需要认证。`/metrics` 使用 `MEMORY_METRICS_TOKEN`，留空时沿用业务令牌；JSON 监控使用业务令牌。模型密钥不回传，页面填写的密钥只保存在进程内。统计接口不调用模型。见 [监控说明](../monitoring/README.md)。
