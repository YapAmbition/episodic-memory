# 记忆切片 · Memory Slice

以「事情」为单位管理共同经历，让 AI 在后续对话中想起与你有关的小事。

它将聊天整理为事件，保留经过、人物、时间与事件之间的关联，再根据当前对话提供相关记忆。如何回复、何时提起这些记忆，交给你的 Agent 决定。

支持独立运行的 **Python 后端 + React 管理页面**，也支持嵌入 AstrBot，无需额外部署服务。

## 选择使用方式

| 方式 | 适合谁 | 入口 |
| --- | --- | --- |
| AstrBot 插件 | 在私聊或群聊中使用记忆 | [安装、配置与打包说明](plugins/astrbot_plugin_memory_slice/README.md) |
| 本机服务与网页 | 管理事件、模拟对话、观察召回效果 | 下方快速启动 |
| Agent Skill | 接入 Codex 等 Agent；目前为本地试用版 | [Skill 安装说明](skills/memory-slice/references/setup.md) |
| HTTP / Python 接入 | 有自己的 Agent 或应用 | [接口说明](docs/backend-api-design.md)、[核心架构](docs/core-architecture.md) |

AstrBot 发布仓库：[memory-slice-astrbot-plugin](https://github.com/YapAmbition/memory-slice-astrbot-plugin)。也可以在本仓库 Actions 的 **Build AstrBot Plugin ZIP** 手动构建，下载 Artifacts 后解压外层 ZIP，将里面的插件 ZIP 上传到 AstrBot。

## 本机快速启动

推荐 Python 3.12，Node.js 20.19+ 或 22.12+。以下脚本适用于 macOS / Linux shell，在项目根目录运行：

```bash
./scripts/dev.sh setup
./scripts/dev.sh start
```

- 页面：<http://127.0.0.1:5173>
- API：<http://127.0.0.1:8000/docs>
- 数据目录：项目下的 `data/`，停止或重启不会清空。

前端以 Vite 开发模式运行，修改源码后自动热更新。常用命令：

```bash
./scripts/dev.sh status
./scripts/dev.sh restart-backend
./scripts/dev.sh stop
tail -f .run/backend.log
```

端口冲突时，在项目 `.env` 中修改 `MEMORY_BACKEND_PORT` 和 `MEMORY_FRONTEND_PORT`；`MEMORY_DATA_DIR` 可指定数据目录。前端代理自动跟随后端端口。日常运行不需要 Docker。

## 第一次使用

1. 创建或选择记忆空间。同一空间内的聊天会话共享事件记忆，不同空间相互隔离。
2. 在「记忆设置 → 模型连接」填写模型地址、模型名和 Key。使用兼容 Chat Completions 的接口。
3. 如需只记住某个人的事情，配置关注对象；普通私聊可以不配置。
4. 在「对话实验室」聊天，再点击整理。此按钮整理当前选中会话；空间级整理也可由定时任务或接口触发。
5. 在事件页面查看整理结果，在「找一件记得的事」或对话中验证召回。

整理是异步的，不是每条消息都生成一个事件。未聊完的事情可暂缓，后续材料补齐后再整理；一次任务也可能更新已有事件或跳过无关内容。达到调用上限或发生错误时会保留进度，按页面提示手动继续。

对话实验室默认实时模式。模拟模式用于观察不同时间下的记忆表现，不会改写已有历史。

## 哪些功能需要模型？

| 功能 | 要求 |
| --- | --- |
| 程序召回 `local` | 不需要模型 |
| 语义增强召回 `hybrid` | 配置兼容 OpenAI Embeddings 的服务 |
| 理解与整理事件、真实聊天 | 配置生成式模型；AstrBot 可复用宿主模型 |
| 无模型体验 | 未配置关注对象时可按会话归档原文，不具备语义事件切分能力 |

配置聊天模型不会自动启用 embedding。生成式 LLM 联想召回已移除。

### 使用 embedding

在记忆设置的「远程语义检索」配置基础地址（例如 `https://example.com/v1`）、模型名和所需密钥，开启后将召回模式设为 `hybrid`。程序自动建立索引，无需另建向量数据库；索引未完成或服务不可用时仍可程序召回。

更换服务、模型或维度后会自动补建索引。已有事件不受影响。AstrBot 模式请在插件配置中修改连接参数并重载，网页中的连接参数只读。

**当前超时设置同时用于召回和后台批量建索引**，默认 1.5 秒。若一直显示「已索引 0、待建若干」且最近错误为超时，可先调到 10 秒验证；这也会增加聊天遇到慢请求时的等待上限。独立超时配置尚未实现。插件网页目前没有连接测试按钮，可查看「最近错误」及索引进度。

### 密钥与访问权限

独立服务中，页面填写的模型 / embedding Key 默认只保存在进程内，重启后需重新填写；也可在 `.env` 设置 `MODEL_API_KEY` / `EMBEDDING_API_KEY`。AstrBot 从插件配置重新加载，Skill 安装器另有系统密钥库选项。

独立服务对外提供访问时，配置 `MEMORY_API_TOKEN` 并使用 HTTPS 反向代理。当前令牌不是空间级多租户授权，不适合直接作为开放注册的公共服务。不要提交 `.env`、密钥或真实聊天数据到 Git。

## 事件、冻结与常驻

- 事件可以关联、追加、编辑，并保留版本。关联网络与时间脉络帮助浏览共同经历。
- 遗忘只降低被唤起的机会，不删除事件正文。
- 冻结保护事件内容，禁止追加和改写；后续进展可另建事件关联。冻结事件仍可召回。
- AstrBot 可选最多 3 件常驻事件，每轮优先加入，仍受总字符预算限制；常驻与冻结是两个独立功能。
- 默认注入上限为 8 件、18000 字符，可配置。长事件可选择相关片段，不必整件塞进上下文。

详细规则见 [记忆机制](docs/memory-mechanisms.md)。

## 数据、复制与跨机器迁移

在侧栏「复制到其他空间」操作：可以复制到本机其他空间，也可以导出 JSON 文件到另一台机器导入。复制后源空间保留，双方独立使用；重复事件可选择跳过或覆盖，冻结目标不能覆盖。

AstrBot 迁移时，先在新平台发送一条消息，让插件创建对应空间，再将文件导入那个空间。空间名称相同不会自动绑定或合并。模型配置、密钥与向量缓存不随文件迁移，需要在目标重新配置。

导入会先备份再事务写入；失败回滚。备份不自动删除。原始聊天与事件分开保存，已消费聊天正文默认保留 30 天后清理，可以在设置中调整；事件正文、历史版本与关联不会因此删除。

操作步骤、迁移限制、异常资料与备份恢复命令见 [数据迁移与恢复](docs/data-management.md)。运行中不要只复制 SQLite 主文件，可能漏掉 WAL 中的数据。

## 排查问题

- **整理暂停**：查看任务详情，区分等待补充材料、达到调用上限和请求失败；不要反复盲点重试。
- **召回为空**：先确认选中了正确空间、已有事件，再看召回依据、关注对象和 embedding 状态。
- **索引不增长**：查看「远程语义检索」中的最近错误，优先检查超时、地址、模型名与密钥。
- **运行监控**：网页展示请求耗时、整理用量、错误与存储指标；详见 [监控说明](monitoring/README.md)。

## 更多文档

- [AstrBot 安装与配置](plugins/astrbot_plugin_memory_slice/README.md)
- [Skill 安装与升级](skills/memory-slice/references/setup.md)
- [HTTP 接口与接入流程](docs/backend-api-design.md)
- [关注对象与群聊接入](docs/focused-memory.md)
- [固定召回评测](evals/recall/README.md)

开发者与 AI Agent 从 [AGENTS.md](AGENTS.md) 开始。历史版本记录不作为当前使用说明。
