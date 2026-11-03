# 记忆切片 · Memory Slice

以「事情」为单位的长期记忆服务，让 AI 在对话中想起共同经历。

记忆切片是一个可独立部署的后端，配套 **React 管理界面和对话实验室**。它记录事情的经过、来源、人物和关联，根据当前对话寻找入口事件，再沿关联读取原文，生成可注入模型的记忆上下文。如何回应、什么时候提起这些记忆，由接入的 agent 自己决定。

当前源码版本：**0.7.0**。技术栈：**FastAPI · SQLite · React · TypeScript · Vite**。

## 功能

- **事件记忆**：详细正文、原始来源、锚点、版本和 `[[事件ID]]` 关联；整理可新建、追加、更正、合并或拆分事件，并非每次整理必定新增一件事。
- **多路召回**：默认程序查找，可选远程 embedding 语义检索；综合内容、时间、鲜明度与人物线索，展开关联后返回上下文。
- **时间与淡忘**：记忆随时间降低可唤起程度，真实重提可以强化；淡忘不删除或改写事件正文。
- **单人关注与群聊**：每个空间可关注一个人，支持名字、别名和平台 UID；消息独立标记发送者、回复对象与 @。未配置关注对象时关闭人物召回权重。
- **后台整理**：手动或定时执行，支持输入预算、长消息分段、旧事件补读、超限缩批和失败诊断。
- **可视化管理**：模拟对话、实时/模拟时间切换、事件编辑、关联网络、记忆导出和召回依据检查。
- **运行监控**：请求、模型用量、召回耗时、整理任务、关注筛选和存储指标；提供 JSON 与 Prometheus 接口。

## 两种运行方式

- **独立服务**：FastAPI + React，默认按下方本机开发步骤启动（不需要 Docker）。
- **嵌入应用**：`pip install .` 安装公共核心，通过 `MemoryService` 调用，不启动 Web 服务。见 [核心接口与生命周期](docs/core-architecture.md)。

已拆分公共核心，并提供 **AstrBot 内嵌插件 0.3.0**，可在插件配置中开启内置 React 管理网页，无需单独部署服务；见 [安装与配置](plugins/astrbot_plugin_memory_slice/README.md)。旧 `llm` 默认模式迁移为程序召回，事件和原始消息保留；早期版本向量数据不复用。`auto` 是 `local` 的兼容别名，`hybrid` 使用可选远程向量并自动降级。

## 获取 AstrBot 插件 ZIP

在 GitHub 仓库的 **Actions → Build AstrBot Plugin ZIP → Run workflow** 手动构建。成功后从该运行的 **Artifacts** 下载，解压外层 ZIP，再把里面的插件 ZIP 上传到 AstrBot。详见 [GitHub 构建说明](plugins/astrbot_plugin_memory_slice/README.md#在-github-上构建-zip)。

## 通过 Skill 接入 Agent（本地试用版）

提供轻量 [Memory Slice Skill](skills/memory-slice/SKILL.md)：服务包独立存放，安装脚本用本地 `cp` 复制、校验 SHA256、建立 Python 虚拟环境并启动服务。Codex 以后台增量同步记录正式对话、Hook 仅召回注入；Qoder、DeepSeek Harness 及其他 Agent 可以按需调用。相同 Agent 的所有会话共享一个空间，未知 Agent 共用通用空间。

开发者在项目根目录构建（需要 Node/npm；安装者只需要 Python 3.11+）：

```bash
python3 scripts/build_skill_bundle.py
```

产物在 `dist/skill-local/`：一个服务 ZIP、一个 Skill ZIP，以及解压好的 `memory-slice/`。Skill 不含服务程序或你的数据。源目录没有 `release.json`，请使用生成的 Skill 目录进行安装。

```bash
# 本机试用；默认 8095，可使用 --port 修改
python3 dist/skill-local/memory-slice/scripts/client.py setup --agent codex
python3 dist/skill-local/memory-slice/scripts/client.py open --agent codex
```

将生成的 Skill 安装到宿主支持的 Skill 目录后，再运行该目录中的同一脚本；若服务 ZIP 不再与 Skill 平级，增加 `--package "/绝对路径/服务包.zip"`。**Hook 安装不等于启用**：Codex 用户需要在 `/hooks` 中审阅并信任定义。无法验证 Hook 能力时脚本明确返回按需模式，不会冒充自动接入成功。

默认使用系统密钥库持久保存模型 Key；没有可用密钥库时可显式传 `--credentials session`，此时重启服务后要在页面重新填写 Key。首次配置模型后，在页面开启定时整理或手动整理。服务自动拉起、版本升级、数据位置、隔离测试和已知限制见 [Skill 安装说明](skills/memory-slice/references/setup.md)。

这套安装器目前在 macOS 验证。Windows/Linux 的平台分支已实现，但尚未完成真实环境验收；Codex 宿主的信任与真实回合触发也需要用户安装后验收。

## 快速启动（本机开发）

需要 Python 3.9+（推荐 3.12）和 Node.js 20.19+ 或 22.12+。在项目根目录运行：

```bash
# 首次安装依赖；已有 .venv 和 node_modules 时不必重复
./scripts/dev.sh setup
# 后台启动 Python 后端和 Vite 开发服务器
./scripts/dev.sh start
```

- 页面：http://127.0.0.1:5173 ，React 源码修改后自动热更新，无需构建镜像或打包前端。
- API：http://127.0.0.1:8000 ，文档 `/docs`，健康检查 `/api/health`。
- 数据保存在项目 `data/`；默认不自动添加示例。

```bash
./scripts/dev.sh status           # 查看两个进程
./scripts/dev.sh restart-backend  # 后端代码修改后重启；前端不受影响
./scripts/dev.sh stop             # 停止两个进程，保留数据
# 查看日志
tail -f .run/backend.log
tail -f .run/frontend.log
```

启动脚本从任意目录调用均可，读取项目根目录 `.env`。可设置 `MEMORY_BACKEND_PORT`（8000）、`MEMORY_FRONTEND_PORT`（5173）和 `MEMORY_DATA_DIR`；前端 API 代理自动跟随后端端口。启动器只停止自己记录且命令匹配的进程，端口被其他服务占用时会报错，不会杀掉其他程序。

数据库配置与事件重启后保留；仅存在内存的模型密钥会随后端重启清除。可在 `.env` 设置 `MODEL_API_KEY` / `EMBEDDING_API_KEY`，或重启后重新在页面填写。`.env`、`.venv`、`data/`、`.run/` 和备份均不提交 Git。

Docker 构建文件仅保留为可选部署资料，日常开发不再构建或启动 Docker。

## 首次配置

1. 创建或选择记忆空间。
2. 在「记忆设置 → 模型连接」填写兼容 Chat Completions 的服务地址、模型名和密钥，保存并测试。
3. 如需只记录某个人的事情，在「关注对象」配置人物；普通 agent 或私聊也可以不配置。
4. 在「整理与提示词」设置整理时机和输入预算；默认召回方式保持 `local`。
5. 在对话实验室发送消息、整理记忆，再用相关线索检查召回结果。群聊可通过「新建群聊试验」模拟不同发送者、回复和 @。

### 哪些功能需要模型？

| 功能 | 模型要求 |
| --- | --- |
| 程序召回 | 无需模型 |
| 语义增强召回 | 选择 `hybrid` 并配置远程 embedding 地址、模型名和密钥；失败自动退回程序召回 |
| 对话实验室真实聊天 | 需要生成式模型；未配置时为标注清楚的规则回放 |
| 理解、切分和更新事件 | 需要整理模型；聊天与整理共用模型连接 |
| 未配置关注对象的本地整理 | 无模型时按会话归档原文，不进行语义事件切分 |
| 开启关注对象的整理 | 需要模型判断关联；未配置时暂缓，消息受临时保留期限约束 |

配置聊天模型不会自动启用语义召回。已移除生成式 LLM 联想召回；整理和聊天仍可使用生成式模型。

### 召回模式

- `local`：只使用程序召回。
- `hybrid`：程序检索与远程向量检索融合，再按人物、时间与鲜明度评分并展开事件链接。向量不可用时返回程序检索结果。

在「召回设置 → 远程语义检索」配置兼容 OpenAI Embeddings 的基础地址（例如 `https://example.com/v1`，自动追加 `/embeddings`）、模型名和密钥。开启后后台每轮最多索引 8 个片段，轮询间隔 5 秒；索引未完成的事件仍可程序召回。修改事件或更换服务、模型、维度后自动补建，不复用过期向量。

请求总超时默认 1.5 秒（含并发排队），不在查询中重试；连续失败 3 次后暂停 30 秒，冷却后允许一个请求探测恢复。超时、网络错误、鉴权失败、限流、无效向量均降级，可从召回轨迹及运行监控查看原因。默认不安装本地模型，也不连接远程向量。

页面提交的向量密钥仅存于服务内存，重启后需重新填写，或使用 `EMBEDDING_API_KEY`。AstrBot 从自己的插件配置重新载入密钥。显式旧 `retrieval_mode="llm"` / `use_model=true` API 请求返回 422，请改用 `local` 或 `hybrid`。


### 环境变量

复制 `.env.example` 后按需修改：

| 变量 | 默认值 / 用途 |
| --- | --- |
| `MEMORY_BIND` | `127.0.0.1`，Compose 对外绑定地址 |
| `MEMORY_PORT` | `8000`，Compose 主机端口 |
| `MEMORY_API_TOKEN` | 业务接口令牌；为空时不要求认证 |
| `MEMORY_METRICS_TOKEN` | `/metrics` 令牌；为空时沿用业务令牌 |
| `MODEL_API_KEY` | 可选生成式模型密钥；模型名与地址在页面配置 |
| `EMBEDDING_API_KEY` | 可选远程向量密钥，供已开启 embedding 的空间使用 |
| `MODEL_MAX_CONCURRENT` | `3`，模型并发上限 |
| `SEED_DEMO` | `true`，首次启动是否创建示例 |

页面填写的模型密钥仅保存在服务进程内，**重启后需要重新填写**；也可以通过环境变量注入。不要将真实 `.env` 提交到 Git。对外部署时应配置令牌及 HTTPS 反向代理。

## 接入自己的 agent

全部业务接口前缀为 `/api/v1`。设置了业务令牌时，携带 `Authorization: Bearer <token>`。

典型流程：

```text
接收聊天消息 → 写入原始消息 → 请求记忆上下文 → agent 生成回复 → 记录回复
                                  ↑
                        后台按设定时机整理事件
```

| 步骤 | 接口 |
| --- | --- |
| 创建记忆空间 | `POST /spaces` |
| 注册 / 查询人物 | `POST /spaces/{sid}/persons`、`GET /spaces/{sid}/persons` |
| 创建会话 | `POST /spaces/{sid}/conversations` |
| 记录消息 | `POST /spaces/{sid}/conversations/{cid}/messages` |
| 获取记忆上下文 | `POST /spaces/{sid}/context` |
| 触发整理 | `POST /spaces/{sid}/maintenance` |
| 查询整理结果 | `GET /spaces/{sid}/jobs/{job_id}` |

获取上下文示例，替换空间 ID 和令牌后运行：

```bash
curl -X POST 'http://127.0.0.1:8000/api/v1/spaces/YOUR_SPACE_ID/context' \
  -H 'Authorization: Bearer YOUR_SERVICE_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{"query":"这次面包又没成功","retrieval_mode":"local","trace":true}'
```

将返回的 `context` 作为记忆资料加入自己的模型请求；`selected`、`candidates`、`seeds`、`omitted` 等字段用于检查结果。请求不提供 `at` 时使用当前时间。未配置关注对象时，无需提供人物参数也能召回。

接入时注意：

- `/chat` 是内置模拟器的完整聊天接口。已有 agent 通常分开使用 `/messages` 和 `/context`，继续使用自己的模型与提示词。
- 群聊消息须逐条提供 `sender_id`；平台 UID 先映射为服务内人物 ID。回复使用服务内消息 ID，提及使用 `mention_ids`。
- `client_id` 用于消息幂等；同一 ID 重试必须保持消息内容、作者、回复和提及一致。
- 收到群消息不等于必须回复；可以记录消息，仅在 agent 准备回应时召回。
- 整理是异步的，新消息不会立刻成为长期事件；agent 仍需管理当前对话的短期上下文。

完整参数和例子见 [后端接口说明](docs/backend-api-design.md)、[单人关注与群聊接入](docs/focused-memory.md)，也可以查看运行实例的 `/docs`。

## 数据与备份

SQLite 保存事件、消息、版本、配置和任务历史。本机开发使用项目 `data/` 目录，重启不会清空数据。旧 Compose 部署使用 `memory-data` 命名卷持久化。普通停止、启动和重建容器会复用卷；**`docker compose down -v` 会删除卷，不要用它更新服务。**

备份应使用 SQLite 在线备份机制，或停止服务后备份完整数据卷；运行中只复制数据库主文件可能漏掉 WAL 数据。升级前先备份，部署时继续挂载原来的卷。

关注模式下，新消息先进入有期限、容量限制的缓冲，相关事件来源长期保留；未被保留的临时原文到期清理。已有历史数据不自动删除。遗忘权重只影响事件召回，与临时消息清理是两套不同机制。

## 本地开发

推荐使用上方 `scripts/dev.sh` 管理进程。也可在两个终端前台启动：

在项目根目录启动后端：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
SEED_DEMO=false PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

前端开发地址为 http://127.0.0.1:5173 ，API 自动代理到 8000。`npm run build --prefix frontend` 后，后端也可直接提供 React 生产页面。

后端默认数据目录为 `data/`，可通过 `MEMORY_DATA_DIR` 覆盖；静态目录可通过 `FRONTEND_DIST` 覆盖。两者均为服务环境变量。

## 测试与监控

安装开发依赖后，在项目根目录运行：

```bash
PYTHONPATH=backend MEMORY_DATA_DIR=/tmp/memory-slice-test-bootstrap SEED_DEMO=false \
  python3 -m unittest discover -s backend/tests -v
npm test --prefix frontend
npm run build --prefix frontend
docker compose config --quiet
```

0.7.0 已通过 127 项核心/接口测试、28 项插件测试、17 项前端测试和生产构建，详见 [测试报告](docs/test-report-0.7.0.md)。模型测试使用可控替身，验证流程与数据一致性，不代表真实模型的语义准确率。

- `GET /api/health`：健康与版本，不需要认证。
- `GET /api/v1/monitoring`：JSON 监控快照，使用业务令牌。
- `GET /metrics`：Prometheus 指标，使用监控令牌。

前端「运行监控」查看耗时、用量、错误和关注筛选；整理任务中可展开失败详情，用任务 ID 对照服务日志中的 `maintenance_failed`。计数器随进程重启归零，长期趋势可接 Prometheus。详见 [监控说明](monitoring/README.md)。

## 当前边界

- 单实例、单 Uvicorn worker；SQLite 与内置调度器尚不支持直接横向扩容。
- 记忆空间用于分组，但当前令牌不是空间级多租户授权；不应据此当作可直接开放注册的托管服务。
- 每个空间最多关注一个人；尚未提供自动跨平台身份合并；已有 AstrBot 适配器与可嵌入 Python 核心。
- 整理只读取预算内的候选旧事，可能漏掉关联或重复建事件；原话校验不能保证模型判断永远正确。
- 记忆衰减和激活分是可调工程模型，不是人类记忆的科学测量或概率。

## 目录与文档

```text
backend/memory_slice/  可嵌入记忆核心、模型适配、整理与召回
backend/app/           独立 HTTP 服务入口（复用 memory_slice/web.py）
backend/tests/     后端机制和接口测试
frontend/src/      React + TypeScript 页面
frontend/tests/    前端工具测试
docs/              接口、记忆机制与版本测试记录
monitoring/        监控说明与抓取配置
Dockerfile         多阶段构建
compose.yaml       服务与持久化卷
.env.example       环境配置模板
```

- [公共核心接口与架构](docs/core-architecture.md)
- [变更记录](CHANGELOG.md)
- [记忆机制、整理预算与遗忘](docs/memory-mechanisms.md)
- [后端接口说明](docs/backend-api-design.md)
- [单人关注与群聊接入](docs/focused-memory.md)
- [监控说明](monitoring/README.md)

### 固定召回质量回归

```bash
.venv/bin/python scripts/evaluate_recall.py --compare evals/recall/baselines/local-v1.json
```

使用隔离临时数据库，默认不联网；28 个固定问题覆盖关键词、语义改写、关联、追问与无答案边界。输出逐题结果、Recall@5、Hit@5、误召回率及耗时，有逐题退步时返回非零退出码。数据范围、真实 Embedding 测试与指标定义见 [召回测试集说明](evals/recall/README.md)。

Codex 同步从首次启用时刻开始，不自动导入此前历史；页面可检查同步状态、排除会话/项目和指定时间段补录。同步记录与模型整理互相独立。

### 整理输出与防循环保护

整理默认输出上限为 8192 token，提示词目标为上限的约 73%，留出 JSON 结构与估算余量。已有显式配置保持不变。输出超限按完整消息减批；单条仍超限则暂停，不切成几十字重试。输入超限单独缩批，后续逐步恢复输入预算。

每轮整理最多 8 次模型调用（含续批、补读和重试），最多 8 批；整理调用关闭底层隐式网络重试。失败、未解决材料、达到上限或服务中断都会保留进度并暂停该空间的自动整理，定时器和重启不会自动反复重试；用户手动整理或重试才开启新一轮。任务详情展示累计调用次数；Prometheus 指标 `memory_maintenance_paused_spaces` 展示暂停空间数。

### 按事件边界推进整理

核心整理支持 `processed_through_message_id`（本批连续处理前缀的最后一个消息 ID，全部等待时为 null）及 `waiting_reason`。边界按会话消息顺序校验，不按 ID 大小比较；前缀中每条消息必须归入事件、强化或带原因的 `ignored_messages`。事件写入和消息处理进度在同一事务提交。旧模型输出未带边界时，仅兼容连续覆盖前缀，空结果仍视为失败。

未完成尾部按会话保存等待状态；相同待处理材料和整理配置不会重复调用模型，新消息或配置变化后可以继续。其他会话独立推进。每批提供少量只读的前序已处理对话；已处理消息不重新计入本批来源。消息仍逐条标记，因此迟到补录不会被游标跳过。

`maintenance_pending_token_limit` 默认 4000（UTF-8 估算 token，可在记忆设置修改）。达到此阈值，或输入预算无法容纳全部待处理消息时，核心要求处理本批全部消息：保存已知阶段性事实，或说明原因并跳过没有可理解信息的内容；不虚构结局，不物理删除聊天。模型未遵守、输出截断或请求失败时保留原文并安全暂停，原有每轮最多 8 次调用限制继续生效。

任务接口新增 `waiting_for_context`、`waiting_reason`、`processed_through_message_id`、`force_progress`、`ignored_messages`。等待任务状态为 completed（本轮已结束，但尾部仍未处理），异常暂停仍为 deferred/failed。监控新增 `memory_maintenance_decisions_total{mode="normal|waiting|forced"}`，按成功提交的批次计数；前端展示等待、跳过原因和容量触发推进情况。

### 已消费聊天原文的定期清理

核心默认开启 `message_cleanup_enabled=true`，`processed_message_retention_days=30`；在「记忆设置 → 整理」可以关闭或调整保留天数。以成功处理完成时的真实 `processed_at` 起算，和实验时间无关；缺少此字段的旧记录从首次启用清理开始获得完整保留期。后台每天检查一次，每批最多 500 条，积压分批继续；服务停止时不运行，下次启动补做检查。关闭自动整理不会关闭原文清理。

只清空已消费消息的 `content`，保留消息身份、处理状态、内容 SHA-256、原长度和 `content_cleared_at`，重复同步相同消息不会恢复正文或重新整理。未完成消息、待整理消息显式回复的原文及最早待整理片段之前的两条已处理消息受到保护；有排队或运行中的整理任务时推迟清理。事件正文、事件历史及关联不修改；事件来源 ID 仍可定位到显示「原文已按保留策略清理」的记录。

空间 `/stats` 的 `message_cleanup` 返回最近执行时间、最近/累计清理数、累计清理字符数和积压数；Prometheus 指标 `memory_consumed_message_bodies_cleaned_total` 为本进程累计清理数。删除正文后不能在本系统用原文重新整理；事件中引用的原话、已有备份不属于本功能的删除范围。SQLite 释放的页可供后续写入复用，数据库文件不保证立即缩小。

### 召回上下文预算

`max_events`（默认 8）限制本轮注入事件总数，包括关联事件；`context_char_budget`（默认 18000）限制完整上下文的字符数。两项均可配置。先去除上下文中已有的同版记忆，再按召回综合评分排序；长事件按片段选择，超限时从低相关片段候选开始移除，直到同时满足两项限制；必要背景与更正依赖组一并保留或移除。规则说明、标签、来源、分隔符及正文都计入预算，不截断事件。一件事件单独仍超限时返回空上下文，并在 `omitted` 中记录原因。

字符数使用 Python `len` 按 Unicode 码点精确计数，不是字节数或 token 估算。接口返回 `context_chars`，监控提供 `memory_context_characters`。不包含宿主另外添加的提示词。两种唤醒模式及 AstrBot 均使用核心限制；AstrBot 在 `memory_options` 中配置上述参数。

### 长事件的部分回忆

`fragment_target_chars` 默认 1500，`fragment_max_chars` 默认 3000，目标不得超过硬上限。先按 Markdown 阶段标题划分，再找段落、句子或换行边界，最后才按字符切开。所有片段可按原顺序无损拼回正文；原始事件不修改。片段是按正文派生的视图，无需重新调用模型整理旧事件。标识由原文起点和内容哈希生成，与召回剩余预算无关；修改正文或分片参数后部分标识会变化。

召回先定位事件，再按当前问题与片段文本的程序相似度排序，暂未利用 embedding 对事件内部片段再次打分。选中片段同时带上事件首段、所在阶段首段、必要前段，以及识别到的更正阶段。更正识别使用规则，不保证识别所有隐含依赖；必要依赖放不下时不输出孤立片段。短事件仍完整注入，同一事件多个片段计一件。

协议规则升级为 v2，部分记忆带 `partial="true"` 和 `parts` 标识，按事件、版本及片段去重。同版片段可以跨轮补充；新版本优先且重新召回，不把部分记忆当完整替换。前端召回卡展示实际片段和原文位置；接口 selected 中增加 partial、injected_body、total_fragments、fragments。监控新增 memory_recall_fragments 与 memory_recall_partial_events_total。

### 结构化召回与上下文组装

新接入使用 `POST /api/v1/spaces/{space_id}/recall`，例如：

```json
{"query":"上次烤面包失败","retrieval_mode":"hybrid","limit":8}
```

`limit` 为最多返回的事件数（1–120），省略时使用空间的 `recall_default_limit`，默认 8。只返回通过现有激活门槛的事件，不凑满。门槛是工程评分而非匹配概率；普通唤醒和主动回想沿用各自门槛。候选池仍受检索配置约束，不保证遍历全库；`truncated` 表示本轮已激活结果超过 limit，候选池截断另见 `retrieval.catalog_omitted_ids` 和 `graph_omitted_count`。

返回 `items`（按分数排序），每项包含 `event_id`、`version`、`score`、完整 `event` 和 `fragments`。片段包含稳定 ID、原文字符位置、正文、评分和依赖片段 ID。片段不另占事件数量。该接口不接收 `current_context`，不进行聊天历史去重，也不受注入字符预算约束，因此响应可能大于 18,000 字符。

Python 接入调用 `await service.retrieve(space_id, RecallInput(...))`，然后在接入侧调用共用函数 `memory_slice.context_assembly.assemble_context(result, config, current_context)`。组装函数根据完整当前上下文去重、处理版本/片段依赖，并应用 `max_events` 和 `context_char_budget`（默认 8 件、18,000 字符）；不发起新检索或模型请求，也不修改原结果。AstrBot 已采用此流程。若希望去重后仍有更多候选，接入方可显式提高检索 limit。

旧 `/context` 和 `service.recall` 作为兼容的一站式上下文接口保留；前端对话实验室及旧调用方仍可继续使用。`/search` 保留原候选查询语义。


### 冻结事件

事件详情提供「冻结事件 / 解冻事件」。冻结独立于 AstrBot 的常驻选择，不占常驻名额；被冻结的事件仍能正常召回，遗忘权重与重提强化仍按原规则运行。

冻结保护已保存的事件内容：自动整理和手工编辑不能追加或改写，合并与拆分需先解冻。整理模型会收到只读标记，后续进展或更正应另建事件并关联原事件；核心写入校验防止模型绕过限制。若模型仍尝试修改冻结事件，该批事务回滚，原始消息保留且不标为已处理。直接删除仍需走原有删除确认；删除或合并其他事件若会改写冻结事件中的引用，则拒绝操作并提示先解冻。

接口：`PUT /api/v1/spaces/{sid}/events/{eid}/freeze`，请求体 `{"frozen": true, "expected_version": 3}`。传 `false` 解冻；版本号可选，提供时用于并发校验，过期返回 409。旧事件默认为未冻结，无需迁移数据库。AstrBot 自带控制台也支持此接口。


### 复制到其他空间

侧栏「复制到其他空间」复制事件、关联、聊天记录、片段和版本历史。源空间完整保留，双方之后独立使用，不自动同步。复制不调用大模型、不合并相似事件；不会复制模型密钥、空间配置、关注对象设置、接入连接或同步进度。

副本获得新的全局唯一 ID，保留 `origin_id` 标记共同来源；旧记录未存此字段时以自身 ID 为来源。重复复制（包括 A→B→C）按来源识别，事件默认跳过，可逐件或批量选择覆盖；消息与会话不会重复添加。覆盖保持目标 ID，保存目标旧版本，按目标自己的版本号递增；双方版本号不能用于判断谁更新。冻结的目标事件禁止覆盖。人物按来源、相同平台与 UID 或已有身份映射复用；同名不自动合并，歧义映射会阻止复制。

预览后确认提交。默认保留目标常驻，可勾选源空间的常驻副本；映射去重后最多 3 件，按复制后的正文校验目标字符预算。数据变化需重新预览；运行中的整理或回复会阻止复制。整个操作原子提交，失败回滚。兼容的向量可复用，否则由目标后台索引补齐（启用远程 embedding 时，后续索引可能调用该服务）。

接口：`POST /api/v1/spaces/{source}/copy/preview`，传 `target_space_id` 获取预览；`POST /api/v1/spaces/{source}/copy` 传目标与 `preview_token` 提交。可选字段：

- `conflict_policy`：`skip`（默认）或 `overwrite`。
- `event_choices`：以源事件 ID 为键、`skip` / `overwrite` 为值，覆盖全局策略。
- `pinned_event_ids`：预览列出的常驻候选 ID；省略保留目标常驻，空数组取消目标常驻。可同时选同源的源/目标候选，提交时映射去重。

旧 `/merge`、`/merge/preview` 空间搬迁接口返回 410，旧 `/bindings` 管理接口已移除。此前已执行的搬迁不会自动恢复。

数据库使用 `PRAGMA user_version` 管理结构版本。首次升级旧数据库前自动备份到数据目录下 `backups/memory-before-schema-1-*.sqlite3`，旧事件 ID 与内容不改写。程序拒绝打开比自身支持版本更新的数据库。兼容指新版读取旧数据；回退到没有版本检查的旧程序时，应恢复升级前备份，不能直接继续写入升级后的数据库。


### 文件迁移（跨机器 / 独立服务与 AstrBot）

在侧栏「复制到其他空间」进入「文件导入 / 导出」。导出一个空间的 `memory-slice-transfer.json`，在另一台机器上传，选择已有空间或创建新空间，校验预览后点击「备份并导入」。顶部「导出记忆」也下载这种迁移包；旧 `/export` 接口仍提供阅读用 Markdown ZIP，不能作为迁移包导入。

- 新建空间使用新 ID；重名自动加“（导入）”后缀。导入现有空间按选定 ID 操作，不按名称自动匹配。
- AstrBot 推荐先在新平台发一条消息，再导入该聊天对应的空间。新建独立空间不会自动绑定群聊。双方配置、关注对象设置和接入连接保留，不迁移密钥、模型配置、同步进度与向量缓存。
- 包含事件、人物、片段、关联、版本历史、冻结状态与常驻候选。统一包含仍保留的聊天正文与整理进度，已移除省略正文选项；已被定期清理的正文无法恢复。旧版省略正文的包会提示重新导出。已导入的旧版省略记录在再次导入完整包时可补全，按正常清理流程清除的正文不会恢复。
- 事件来源标识跨机器保留。重复事件默认跳过，可逐件覆盖；覆盖保留目标 ID 与旧版本，冻结目标禁止覆盖。常驻默认保留目标选择，可选来自文件的常驻副本，仍校验 3 件和字符预算。
- 目前格式版本 1，单包最大 32 MiB、最多 100000 条记录。文件为 UTF-8 JSON，含 SHA-256 完整性校验；校验用于发现损坏，不是来源签名或加密。
- 预览只暂存目标空间和空间名称列表，不复制其他空间正文。正式导入只做一次整库备份，通过 WAL 只读快照完成，不持有数据库写锁；其他空间可继续写入。差异计算在写锁之外完成，最终核对目标版本并在单个事务中写入。失败整体回滚。临时文件自动清理；备份不会自动删除，需要预留磁盘空间。
- 导入有独立操作编号。连接中断时先点「查询上次导入结果」，刷新同一标签页后也能查询。同一编号、相同请求重试返回原结果；不同内容使用相同编号会拒绝。

接口：`GET /api/v1/spaces/{sid}/transfer` 导出（旧参数 `include_messages=false` 返回 422）；`POST /api/v1/transfers/operation-id` 获取操作编号，兼容普通 HTTP；`POST /api/v1/transfers/preview` 和 `/transfers/import` 接收 `{package, operation_id, target_space_id}`，新建时省略目标并可传 `new_space_name`。导入需带预览的 `preview_token`，冲突与常驻选择沿用复制接口的 `conflict_policy`、`event_choices`、`pinned_event_ids`（ID 使用预览返回值）。`GET /api/v1/transfers/operations/{operation_id}` 查询成功结果。AstrBot 所有接口仍需控制台令牌。

#### 从导入前备份恢复

成功页面显示服务端完整备份路径，位于数据目录 `backups/before-import-*.sqlite3`。备份名包含 UTC 时间、操作类型、目标空间名（恢复备份使用数据库名）和唯一标识，方便辨认；所有备份保留，不自动删除。这是整库快照，包含其他空间；恢复会回到快照时刻。先停止独立后端（`./scripts/dev.sh stop`）或整个 AstrBot，保留导入后的新聊天，再运行：

```sh
python scripts/restore_memory_backup.py --backup "/备份路径/before-import-xxx.sqlite3" --database "/数据目录/memory.sqlite3" --service-stopped
```

使用安装了项目依赖的 Python。AstrBot 插件目录内也包含 `restore_memory_backup.py`，在插件目录运行时去掉命令中的 `scripts/`；使用 AstrBot 的 Python 环境。Windows 路径同样放在引号内。

恢复工具先获取与服务共享的跨进程互斥锁，运行中的新版服务会阻止恢复，恢复期间也不能启动服务。随后检查备份表结构、主键、空间配置及业务记录，校验通过才把当前数据库另存为 `backups/before-restore-*.sqlite3`，然后通过 SQLite 备份接口恢复并检查完整性。旧版服务尚无互斥锁，升级或恢复前仍须停止；不要在服务运行时执行。工具完成后再启动服务；如需撤回恢复，可以用刚保存的 before-restore 文件重复上述流程。

旧版迁移包遗漏的消息人物字段会在导入时兼容补全；对于已经导入的记录，服务启动时先保存 `backups/before-message-identity-repair-*.sqlite3` 整库备份，再补全缺失字段。只根据发送人、私聊对象或明确回复关系恢复，无法确认的群聊身份保留为未知；已有身份、聊天正文和消费进度不会被覆盖。
