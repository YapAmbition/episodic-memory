# 核心架构与开发决策

开发入口为 [AGENTS.md](../AGENTS.md)，用户启动方式见 [README](../README.md)。本文描述当前分工，不以历史发布版本为边界。

## 一份核心，多个宿主

| 位置 | 职责 |
| --- | --- |
| `backend/memory_slice/service.py` | `MemoryService` 公共入口、生命周期、事务编排和整理调度 |
| `store.py`、`migrations.py`、`database_guard.py` | SQLite、结构升级、数据库占用保护 |
| `retrieval.py`、`engine.py`、`embedding.py` | 程序候选、关联传播、评分与远程向量 |
| `recall_fragments.py`、`event_fragments.py` | 事件片段、相关度及依赖 |
| `context_assembly.py`、`context_protocol.py`、`pinned_context.py` | 上下文组装、协议去重、常驻预算 |
| `maintenance*.py`、`model.py`、`adapters.py` | 事件整理、模型预算与适配 |
| `focus.py`、`message_identity.py`、`message_retention.py` | 关注筛选、身份与消息清理 |
| `space_copy.py`、`transfer.py`、`migration_compensation.py`、`backup_validation.py` | 复制、文件迁移、缺失资料补偿与备份验证 |
| `backend/memory_slice/integrations/` | Agent 历史同步等接入功能 |
| `backend/memory_slice/web.py`、`backend/app/` | HTTP 适配与独立服务入口 |
| `plugins/astrbot_plugin_memory_slice/` | AstrBot 生命周期、身份映射、消息采集与注入 |
| `skills/memory-slice/`、`scripts/` | Skill 客户端、安装器、构建及发布工具 |
| `frontend/src/` | React 控制台，独立服务与插件共用；图使用 G6 |

`pip install .` 可只安装 Python 核心，不要求 FastAPI。HTTP 运行依赖见 `backend/requirements.txt`。核心默认导入不创建 Web 应用、不监听端口、不写入示例数据。

宿主创建 `MemoryService(data_dir, model=adapter)`，加载时 `await start()`，卸载时 `await close()`。同一数据目录只允许一个活跃服务；保持数据库占用保护，不能以多个 worker 或多个宿主进程同时写入来扩容。后台任务需正确取消并等待退出。

## 数据流

```text
宿主真实消息 → 幂等入库 → 后台整理 → 事件、片段、关联、版本
                               ↓
                         后台向量索引（可选）
当前提问 → retrieve / recall HTTP → 结构化记忆
                                   ↓
               宿主实际上下文 → assemble_context → 记忆注入 → 宿主回复
```

空间隔离记忆，会话组织原始消息；同一空间可有多个会话。事件与聊天分开，消息逐条保留消费进度，不能只靠时间游标判断是否整理过。整理候选受预算约束，不读取所有旧事件；事件写入和消息进度在同一事务提交。

## 检索与上下文分工

新接入调用 `await service.retrieve(sid, RecallInput(...))`，HTTP 对应 `/spaces/{sid}/recall`。返回按分数排序的 `items`，包含完整事件、版本、分数及可选片段。`limit` 默认取空间配置 `recall_default_limit=8`，范围 1–120，达不到激活门槛不补满。

这个接口不接收 `current_context`，不做对话去重，也不限制注入字符数。宿主掌握真正送入模型的上下文，负责将其传给共享纯函数：

```python
from memory_slice.schemas import RecallInput
from memory_slice.context_assembly import assemble_context

result = await service.retrieve(sid, RecallInput(query="上次烤面包失败", limit=8))
assembled = assemble_context(result, service.config_for(sid), current_context)
```

`current_context` 是当前请求中实际存在的文本块列表，不是永久聊天档案。组装执行版本/片段去重、依赖选择和条数/字符预算，不重新检索、不调用模型。AstrBot 使用此流程；常驻选择另由插件结合 `pinned_context.py` 处理。

`service.recall` / HTTP `/context` 保留兼容的一站式路径，前端实验室仍可使用。`/search` 是限量入口候选查询，不等价于完整关联召回。

记忆格式使用 `<memory-slice>`，规则使用带版本的 `<memory-slice-rules>`；当前片段协议为 v2。相同事件以较新 revision 为准，同版不同片段可以补充；部分片段不能当作完整事件替换。规则块只在当前上下文缺少对应版本时补充。协议解析不是安全认证，宿主仍需把记忆标为参考资料。

## 模型与向量

`ModelAdapter.complete(settings, api_key, messages, json_mode=False, purpose="other", metrics=None, max_tokens=None)` 返回模型文本。用于整理与模拟聊天；输出截断、上下文超限分别用 `OutputLimitError`、`ContextLimitError`，部分 JSON 不能入库。模型用量缺失时不编造统计。

embedding 是独立连接，不经过宿主生成式模型适配器。模型签名由地址、模型名和维度组成；片段内容哈希检查新旧。事件文本按 1200 字符、1000 步长生成向量片段，每批最多 8 个，每轮间隔 5 秒。它与上下文输出片段（目标 1500、最大 3000 字符）是两种不同用途的分片，不能混淆。

向量保存在 SQLite，当前为精确余弦扫描，无独立向量数据库。更换模型补建，错误退回程序召回；索引失败不删除事件。状态与熔断由 `embedding.py` 管理，具体默认值以 `EmbeddingInput` 为准。

## 身份和迁移决策

人物注册使用内部 ID，平台与外部 UID 作为接入映射。召回比较 UID 时忽略平台，空间隔离仍含平台信息；这是兼容更换连接方式后历史记忆的产品取舍，不意味着所有身份操作都忽略平台。

复制与导入使用新全局 ID，`origin_id` 追踪共同来源，保持两个空间可独立修改。按来源处理重复，不按显示名合并。补偿共用规则，不能为通过校验而丢弃历史原文；不确定资料进入 `migration_recovery`，保留原始记录。详见 [数据迁移与恢复](data-management.md)。

## 已知问题与边界

- **尚未拆分 embedding 超时**：查询和每批索引共用 `timeout_seconds`（默认 1.5 秒），长片段批量索引可能持续超时。调大可缓解，但也放宽聊天等待上限。独立索引超时是待办，不能宣称已实现。
- **AstrBot 连接测试入口待完善**：只读配置页仍隐藏连接测试按钮。插件已提供独立轮转诊断日志与监控页面查看/下载入口；核心通过实例级诊断接收器报告，不依赖宿主 Python 日志配置。
- 单实例 SQLite，精确向量扫描未验证大规模负载；服务令牌不是空间级租户授权。
- 事件候选、语义判断、片段依赖识别均可能漏召回；默认回归用替身不能证明真实模型质量。
- Skill 安装器以 macOS 验证为主，其他平台与真实宿主兼容性须单独验收。Codex 同步从启用时刻开始，不自动导入全部历史。

历史测试报告见 [历史目录](history/README.md)，不要用历史报告替代当前验证。
