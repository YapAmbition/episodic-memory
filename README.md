# 记忆切片 · Memory Slice

以「事情」为单位的长期记忆服务，让 AI 在对话中想起共同经历。

记忆切片是一个可独立部署的后端，配套 **React 管理界面和对话实验室**。它记录事情的经过、来源、人物和关联，根据当前对话寻找入口事件，再沿关联读取原文，生成可注入模型的记忆上下文。如何回应、什么时候提起这些记忆，由接入的 agent 自己决定。

当前源码版本：**0.6.0**。技术栈：**FastAPI · SQLite · React · TypeScript · Vite**。

## 功能

- **事件记忆**：详细正文、原始来源、锚点、版本和 `[[事件ID]]` 关联；整理可新建、追加、更正、合并或拆分事件，并非每次整理必定新增一件事。
- **多路召回**：默认程序查找，可选显式大模型联想；综合内容、时间、鲜明度与人物线索，展开关联后返回上下文。
- **时间与淡忘**：记忆随时间降低可唤起程度，真实重提可以强化；淡忘不删除或改写事件正文。
- **单人关注与群聊**：每个空间可关注一个人，支持名字、别名和平台 UID；消息独立标记发送者、回复对象与 @。未配置关注对象时关闭人物召回权重。
- **后台整理**：手动或定时执行，支持输入预算、长消息分段、旧事件补读、超限缩批和失败诊断。
- **可视化管理**：模拟对话、实时/模拟时间切换、事件编辑、关联网络、记忆导出和召回依据检查。
- **运行监控**：请求、模型用量、召回耗时、整理任务、关注筛选和存储指标；提供 JSON 与 Prometheus 接口。

## 两种运行方式

- **独立服务**：FastAPI + React，按照下方 Docker 或本地开发步骤启动。
- **嵌入应用**：`pip install .` 安装公共核心，通过 `MemoryService` 调用，不启动 Web 服务。见 [核心接口与生命周期](docs/core-architecture.md)。

已拆分公共核心，并提供 **AstrBot 内嵌插件 0.1.0**，无需单独部署服务；见 [安装与配置](plugins/astrbot_plugin_memory_slice/README.md)。旧数据库的语义默认模式会迁移为程序召回，事件和原始消息保留；旧向量数据不再使用。`auto` 请求仍作为 `local` 的兼容别名，`hybrid` 请求不再支持。

## 快速启动

需要安装 Docker 和 Docker Compose。在项目根目录运行：

```bash
cp .env.example .env
docker compose up --build -d
```

启动后访问：

| 地址 | 用途 |
| --- | --- |
| http://127.0.0.1:8000 | 管理页面与对话实验室 |
| http://127.0.0.1:8000/docs | 交互式 API 文档 |
| http://127.0.0.1:8000/api/health | 健康检查 |

首次启动默认导入**虚构示例**，可以在页面创建自己的空白记忆空间。如果不需要示例，在首次启动前将 `.env` 中的 `SEED_DEMO` 改为 `false`。

常用命令：

```bash
# 查看运行情况和日志
docker compose ps
docker compose logs -f memory

# 停止、启动已有容器
docker compose stop
docker compose start

# 修改或更新源码后，重新构建并替换容器
docker compose up --build -d
```

`restart` 只重启现有容器，不会加载修改后的源码。上面的 Compose 命令管理从本项目启动的 Compose 实例，不会更新其他手工创建的容器。

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
| 大模型联想召回 | 显式启用 `llm`，使用生成式模型 |
| 对话实验室真实聊天 | 需要生成式模型；未配置时为标注清楚的规则回放 |
| 理解、切分和更新事件 | 需要整理模型；聊天与整理共用模型连接 |
| 未配置关注对象的本地整理 | 无模型时按会话归档原文，不进行语义事件切分 |
| 开启关注对象的整理 | 需要模型判断关联；未配置时暂缓，消息受临时保留期限约束 |

配置聊天模型不会自动启用大模型召回。本版已移除 embedding 功能。

### 召回模式

- `local`：只使用程序召回。
- `llm`：显式调用生成式模型扩展线索、判断关联，增加耗时与用量。


### 环境变量

复制 `.env.example` 后按需修改：

| 变量 | 默认值 / 用途 |
| --- | --- |
| `MEMORY_BIND` | `127.0.0.1`，Compose 对外绑定地址 |
| `MEMORY_PORT` | `8000`，Compose 主机端口 |
| `MEMORY_API_TOKEN` | 业务接口令牌；为空时不要求认证 |
| `MEMORY_METRICS_TOKEN` | `/metrics` 令牌；为空时沿用业务令牌 |
| `MODEL_API_KEY` | 可选生成式模型密钥；模型名与地址在页面配置 |
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

SQLite 保存事件、消息、版本、配置和任务历史，Compose 使用 `memory-data` 命名卷持久化。普通停止、启动和重建容器会复用卷；**`docker compose down -v` 会删除卷，不要用它更新服务。**

备份应使用 SQLite 在线备份机制，或停止服务后备份完整数据卷；运行中只复制数据库主文件可能漏掉 WAL 数据。升级前先备份，部署时继续挂载原来的卷。

关注模式下，新消息先进入有期限、容量限制的缓冲，相关事件来源长期保留；未被保留的临时原文到期清理。已有历史数据不自动删除。遗忘权重只影响事件召回，与临时消息清理是两套不同机制。

## 本地开发

需要 Python 3.9+，Node.js 20.19+ 或 22.12+。Docker 构建使用 Python 3.12 与 Node.js 24。

在项目根目录启动后端：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
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

0.6.0 已通过公共核心与 HTTP 适配回归测试、前端测试和生产构建，详见 [测试报告](docs/test-report-0.6.0.md)。模型测试使用可控替身，验证流程与数据一致性，不代表真实模型的语义准确率。

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
backend/app/           FastAPI 路由、鉴权和 HTTP 中间件
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
