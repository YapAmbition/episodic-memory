# 记忆切片 · Memory Slice for AstrBot

插件版本 **0.3.20**，内嵌 Memory Slice **0.7.0** 核心。无需另外部署 Docker 或 HTTP 服务。默认程序召回，可选远程 embedding。模型整理复用 AstrBot 已配置的聊天模型。

## 安装

要求 **AstrBot >= 4.24.0**，使用其内置 LLM/Agent 流程。该最低版本来自本轮临时内容块 API；不支持绕过版本限制安装到旧版。外接 Dify 等未触发标准请求钩子的执行器尚未适配。

1. 在 AstrBot 插件管理页面选择从本地 ZIP 安装，上传 `astrbot_plugin_memory_slice-v0.3.20.zip`。
2. 如果使用手工安装，将 ZIP 中的 `astrbot_plugin_memory_slice` 文件夹放到 AstrBot 的 `data/plugins/` 下；在 AstrBot 的 Python 环境安装该目录的 `requirements.txt`，然后重载插件。
3. 插件配置中选择已有聊天模型；留空时跟随当前会话。无需填写新的 API Key 或模型地址。
4. 私聊可以直接测试。群聊填写 `focus_uid`（要关注的人的平台 UID），建议同时填写 `focus_name` 和 `group_ids`。
5. 保存配置后重载插件，使当前实例和后台调度一起应用新配置。

首次安装依赖可能需要联网下载 Python 包。ZIP 包含记忆核心源码，不需要从 PyPI 下载一个未发布的 memory-slice 包。发布仓库为 https://github.com/YapAmbition/memory-slice-astrbot-plugin 。首次自动发布成功后，可以在 AstrBot 使用该仓库链接安装。开发仓库 `episodic-memory` 的插件子目录仍需先构建，不能直接用开发仓库链接安装。插件市场收录需要另行提交。

## 配置

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| enabled | true | 总开关 |
| private_enabled | true | 记录私聊 |
| group_enabled | true | 允许群聊；未配置关注 UID 时仍跳过群聊 |
| focus_uid | 空 | 唯一关注对象的稳定 UID；不要填写昵称 |
| focus_name | 空 | 显示名及正文提及的辅助线索，不替代 UID |
| focus_platform | 空 | UID 所属的平台实例 ID；空值应用于所有实例，多平台时建议明确填写 |
| group_ids | [] | 群 ID 白名单；空列表允许所有群 |
| provider_id | 空 | AstrBot 已配置的聊天模型；空值跟随会话 |
| retrieval_mode | local | local 程序多路召回；hybrid 程序 + 远程向量，异常自动降级 |
| auto_maintain | true | 开启后台定时整理 |
| interval_minutes | 60 | 整理间隔，1–10080 分钟 |
| model_timeout_seconds | 60 | 单次记忆模型调用超时 |
| context_window_tokens | 0 | 模型上下文容量；0 为未知，使用保守输入预算 |
| output_tokens | 8192 | 整理输出预留 |
| ignore_prefixes | / 等 | 忽略指令消息；本插件管理指令始终忽略 |
| memory_options | {} | 核心高级参数 JSON |

例如高级参数：

```json
{
  "half_life_days": 21,
  "activation_threshold": 0.26,
  "max_seeds": 3,
  "max_depth": 3,
  "max_events": 8,
  "context_char_budget": 18000,
  "fragment_target_chars": 1500,
  "fragment_max_chars": 3000,
  "maintenance_input_token_budget": 8000,
  "maintenance_forgetting_weight": 0.65,
  "focus_buffer_hours": 24,
  "focus_buffer_limit": 2000
}
```

其余可用键以核心 `MemoryConfig` 为准；未知键会拒绝加载。关注对象和 schedule_* 参数请使用插件专用配置，不能在高级 JSON 中覆盖。程序召回不调用模型；后台整理仍需要模型。无法从 AstrBot 自动得知真实上下文容量，需要用户按所选模型填写。

## 管理指令

需要 AstrBot 管理员权限：

- `/记忆身份`：查看当前发送者 UID、平台实例 ID、群 ID，便于填写配置。
- `/记忆切片`：查看当前空间事件数、待整理数、最近整理任务状态和安全错误信息。
- `/记忆整理`：提交后台整理任务，不阻塞正常聊天。随后用状态指令查看结果。

指令不回显记忆正文。自动整理没有群内通知。模型调用计数、耗时和真实 token 用量由核心监控记录；宿主可通过 `runtime.core.metrics.snapshot()` / `prometheus()` 读取，启用下方管理网页后，也可通过带访问令牌的 `/api/v1/monitoring` 和 `/metrics` 查看。

## 内置管理网页

插件 ZIP 已包含 React 页面，不需要单独启动 Memory Slice 后端或 Docker。网页和聊天插件使用**同一个运行中的记忆核心**，读取同一份数据。

在 AstrBot 插件配置中设置后重载插件：

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| web_enabled | false | 开启管理网页 |
| web_host | 127.0.0.1 | 监听地址；局域网访问或容器内使用时按需设为 0.0.0.0 |
| web_port | 8765 | 网页及 API 端口 |
| web_token | 空 | 自行设置至少 16 位 ASCII 字符的管理令牌；不同于模型密钥 |

在 AstrBot 所在电脑打开 `http://127.0.0.1:8765`，输入上述令牌。其他电脑访问时使用 AstrBot 主机的局域网 IP，并配置相应端口映射/防火墙；`0.0.0.0` 是监听地址，不是浏览器访问地址。令牌具有全部记忆空间的管理权限，跨不可信网络访问应使用 HTTPS 反向代理或 SSH 隧道。

- **插件会话**：按群/私聊切换空间，查看原始消息，每 5 秒刷新；可程序检索记忆，不会发送模拟聊天消息。
- **事件与关联网络**：查看正文、来源和关系，支持编辑事件与导出。
- **整理与监控**：手动整理、查看任务失败详情、模型用量和运行指标。旧关注身份的停用空间可查看，但不能启动整理。
- **记忆设置**：只读展示；模型、关注对象、定时整理等继续在 AstrBot 配置，保存后重载插件。

端口冲突、缺少前端资源或令牌不合要求时，网页启动失败会写入 AstrBot 日志，也可用 `/记忆切片` 查看；记忆插件继续运行。重载/卸载插件会关闭网页并释放端口。关闭网页不会清空数据。

## 实际行为与隔离

- 从平台接收的文本、发送者、@ 和已观测回复关系进入核心。图片、语音、转发嵌套正文不进行 OCR/转写。
- 群聊监听范围是 AstrBot 实际分发给插件的消息，不会主动拉取群历史。未提及机器人的消息能否到达插件取决于平台适配器和 AstrBot 配置。
- 群聊按关注 UID、提及、回复和名字筛选候选，再由整理模型确认事件归属。附近上下文可暂存，非相关消息按核心期限和数量上限清理。
- 不配置关注 UID 时，私聊照常记录，人物召回权重关闭；群聊不启用。配置 UID 后，匹配平台的私聊也遵循只记与该对象有关的规则。
- 按平台实例、机器人账号、群 ID / 私聊 UID、关注身份隔离空间。一个群的不同成员共享本群空间，不与其他群或私聊共享。不同会话模型未指定 provider_id 时，以该群最近接收消息的 UMO 选择后台模型。
- 改关注 UID 或平台实例范围后建立新空间，旧空间保留但停止调度和自动恢复，不混入新对象的记忆。改回原配置可重新使用原空间。
- AstrBot 清空 LLM 历史不清空长期事件；本插件会话持续记录。跨群共享同一个人的经历、人格级隔离和多关注对象不在首版范围内。
- 召回结果作为 `TextPart.mark_as_temp()` 注入本轮请求，不修改系统人设和已有历史。插件只提供记忆资料，不决定模型必须说什么。
- 记录原始输入，不把注入的记忆当成新消息重复整理。仅在收到完整成功的 LLM 回复并触发发送后钩子时记录回复；错误、工具结果、单独流式分片不记入。不同平台的流式发送需要实机验证。
- 模型失败保留原消息；记忆钩子失败记录错误但不阻断聊天。单个插件实例持有数据库锁，避免重载时两个实例同时调度。

## 数据与备份

存储于 AstrBot 数据目录下：

```text
plugin_data/astrbot_plugin_memory_slice/
  memory.sqlite3
  runtime.lock
```

更新插件不会覆盖此目录。停止插件后备份整个数据目录，或使用 SQLite 在线备份。不要直接复制正在写入的主数据库文件而遗漏 WAL，也不要让独立 Web 服务同时操作插件数据库。现有 8092 服务的数据不自动导入插件，插件从自己的空数据库开始。

## 在 GitHub 上构建 ZIP

仓库提供 `.github/workflows/build-astrbot-plugin.yml`，无需单独创建插件发布仓库，也不需要在本机安装 Node.js。

1. 将项目代码及工作流文件推送到自己的 GitHub 仓库。工作流必须先出现在默认分支，才能在页面手动运行。
2. 打开 **Actions → Build AstrBot Plugin ZIP → Run workflow**，选择包含最新修改的分支，启动构建。
3. 工作流安装 Python 3.12、Node.js 24 和依赖，测试前端、构建插件 ZIP，再运行插件与后端测试；全部成功才上传安装包。
4. 打开成功的运行记录，在 **Artifacts** 下载 `memory-slice-astrbot-运行编号`。下载需要登录 GitHub，并拥有仓库读取权限。
5. **先解压 GitHub 下载的外层 ZIP**，里面的 `astrbot_plugin_memory_slice-v版本.zip` 才是给 AstrBot 上传的安装包。

构建产物保留 90 天（也受仓库/组织上限约束）。手动运行只生成下载产物，不推送发布仓库、不创建 Release，也不提交插件市场。需要长期对外提供下载时，可以把内层插件 ZIP 手动附到 GitHub Release；公开仓库的 Release 下载更方便。

手动构建 ZIP 不需要填写模型 API Key 或新增 GitHub Secrets，测试使用模拟宿主。GitHub 构建通过不代表 Windows + AstrBot 实机已经验收。

## 自动同步插件发布仓库

开发仍在 [episodic-memory](https://github.com/YapAmbition/episodic-memory) 完成。发布仓库由自动流程生成，不要在那里直接修改业务代码。

一次性准备：在开发仓库 **Settings → Secrets and variables → Actions** 添加 `PLUGIN_PUBLISH_TOKEN`。使用 Fine-grained token，只授权 `YapAmbition/memory-slice-astrbot-plugin` 的 **Contents: Read and write**；不要把令牌写入代码。到期后更新同名 Secret。

发版步骤（以下以当前 0.3.20 为例）：

1. 确认 `metadata.yaml`、`main.py` 注册版本及说明中的版本一致；后续发布必须递增版本。
2. 将代码和工作流提交、推送到开发仓库。
3. 在要发布的提交上执行：

```bash
git tag astrbot-v0.3.20
git push origin astrbot-v0.3.20
```

标签版本必须与 `metadata.yaml` 完全一致。普通分支 push 不触发发布，页面 Run workflow 仍只构建 ZIP。

自动流程先运行前端测试、构建完整 ZIP、运行插件与后端测试；全部通过后，独立发布任务下载该 ZIP，将内容放入发布仓库根目录，更新 `main` 并创建同名标签。包含已编译前端及完整核心，不同步数据库、聊天记录、密钥、node_modules 或开发工作流。`release-info.json` 记录来源提交。

发布不使用 force push；分支和标签原子推送。拒绝版本倒退、标签来源冲突及覆盖未知仓库内容。同一标签同一来源的重跑不会重复发布；凭据或网络失败可修好后在 Actions 重跑失败任务。目标分支若有保护规则，需允许发布身份正常推送，否则流程会失败且不覆盖远端。

首次成功后，用发布仓库链接在 AstrBot 测试安装，再前往 AstrBot Cloud 提交市场收录；自动同步不会替你提交市场审核。

## 开发与测试

构建需要 Python 3.9+ 与 Node.js 20.19+ 或 22.12+；安装已打包 ZIP 的 AstrBot 主机不需要 Node.js。在 Memory Slice 项目根目录：

```bash
python3 scripts/build_astrbot_plugin.py
python3 -m unittest discover -s plugins/tests -v
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -q
```

构建脚本先在本机执行 React 生产构建（每次先执行 `npm ci`），把页面静态资源放进 ZIP，再从 `backend/memory_slice/` 复制核心到 ZIP，生成 SHA-256 清单，避免手工维护两份业务逻辑。开发者修改核心后需重新构建插件；源码目录的相对 `.memory_slice` 在构建时补入。

默认输出在项目内的 `dist/astrbot_plugin_memory_slice-v0.3.20.zip`（可用 `--output` 指定位置）。已有与源码匹配的前端构建时可加 `--skip-frontend-build`；脚本校验源码指纹，过期资源会拒绝打包。

当前通过 25 项插件测试、113 项核心/HTTP 回归测试和 17 项前端工具测试。新增覆盖网页鉴权、同实例共享、只读配置、事件编辑、整理、端口释放及重启。插件测试使用真实共享核心、SQLite 和模拟 AstrBot 宿主，覆盖事件整理/召回、群聊筛选与隔离、重载、去重、超时取消、模型输出截断、临时注入、发送后记录、故障不阻断聊天及包内核心一致性。

**尚未在实际运行的 AstrBot + QQ 群环境联调，不将模拟测试等同于实机通过。** 首次验收建议使用测试群：配置关注对象，聊一件有独特细节的事，执行整理并确认完成，再用相关表达触发回忆；换另一个群确认不串记忆，重载后确认仍能召回。流式模式、@/回复和不唤醒机器人时的群消息到达情况也应一并验证。

接口依据：[AstrBot 消息钩子](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)、[模型接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[插件配置](https://docs.astrbot.app/dev/star/guides/plugin-config.html)、[数据目录](https://docs.astrbot.app/dev/star/guides/storage.html)。

## 可选远程向量召回

在 AstrBot 插件配置设置 `retrieval_mode=hybrid`、`embedding_enabled=true`，填写 `embedding_base_url`（例如 `https://example.com/v1`）、`embedding_model` 和 `embedding_api_key`，保存并重载插件。调用的是该地址的 `/embeddings`，不复用聊天模型，不下载本地模型。

`embedding_timeout_seconds` 默认 1.5 秒，当前召回和后台索引共用；索引持续超时时可调到 10 秒验证，但同时会放宽聊天等待上限。独立索引超时尚未实现。`embedding_failure_threshold` 默认 3 次，`embedding_cooldown_seconds` 默认 30 秒。超时、连接失败、401/403、429、服务器错误及错误向量响应均退回程序召回，不阻止聊天注入和回复保存。冷却后允许单个探测请求，成功自动恢复。

后台自动分片建立索引，事件编辑、更换模型后补建，未就绪时使用程序查找。`/记忆切片` 可查看索引进度和安全错误分类，管理网页的设置页只读展示连接状态，运行监控展示索引和熔断指标。群/私聊仍按原有空间隔离。旧 `llm` 配置升级为 `local`；生成式模型仅负责整理，召回不调用它。


### 插件侧召回与去重

在 AstrBot 配置中设置 `recall_limit`（默认 8，范围 1–120）和 `context_char_budget`（默认 18000，范围 1000–100000 字符）。插件每次向核心结构化召回入口显式传入 `limit=recall_limit`；长度是插件注入预算，不是核心检索的过滤条件。

插件读取 ProviderRequest 当前实际可见的系统提示、全部历史、当前输入和额外文本，在本地调用共用组装函数完成事件/片段版本去重，再应用条数及字符预算，最后临时注入。规则和标签也计入字符数；完整同版记忆不重复注入，同版缺失片段可补充，新版会标明更新关系。不会把完整历史交给核心进行去重。只检查当前请求快照，不用永久“已发送”标记，记忆从上下文移除后可以再次注入。

去重后可能少于设置条数或为空，不会循环补查。未提供 ProviderRequest 时无法判断现有上下文，仍执行数量和长度限制。旧安装若没有这两个专用字段，兼容读取高级 JSON 的 `max_events`/`context_char_budget`；专用字段存在时优先采用专用字段。插件使用 `retrieve` + 本地组装流程；当前记忆片段协议为 v2。


0.3.3：插件最终注入指标使用 `memory_plugin_injections_total`、`memory_plugin_events`、`memory_plugin_duration_seconds`；`/记忆切片` 展示最近一次结果。管理页面显示插件的每轮实际限制。近期线索按入库顺序限量读取，引用按 ID 查找。失败日志含阶段、错误分类、诊断编号及代码位置，不输出异常正文。


### 常驻事件（最多 3 件）

打开插件自带 Web 控制台，选择对应群聊/私聊空间，在「记忆设置 → 常驻记忆」勾选事件并保存；不需要重载插件。选择按空间存储，插件重启后保留。取消全部选择并保存即可关闭。

每次模型请求都完整注入所选事件的最新版本，不受相关性门槛和上下文去重影响。常驻最多 3 件，普通召回仍最多 `recall_limit` 件；常驻优先占用共同的 `context_char_budget`，普通召回使用剩余容量。同一事件不会在本轮常驻与普通召回中重复出现。常驻按 ID 直接读取；普通召回或拼接失败时仍提供有效常驻记忆，不向聊天窗口发送错误。降级记录在日志、失败指标与最近注入状态中，可主动通过 `/记忆切片` 查看。

保存时在同一写事务内读取最新事件、校验并保存，写锁最多等待 1 秒，不自动重试。数据库忙时保留原设置，设置页保留未保存的勾选并提供「重试保存」，不打扰聊天。保存时按实际输出校验容量，包含转义后的正文、标题、时间、状态、规则和标签。超过上限、超过 3 件、跨空间、已删除或已归档均拒绝保存，原选择不变。如果保存后事件增长或降低上限导致超限，暂停当轮记忆注入；聊天继续，`/记忆切片` 和选择页面提示调整，绝不静默截断常驻正文或突破上限。普通聊天需要正常经过 AstrBot 的模型请求钩子。

插件管理接口（沿用控制台认证）：`GET /api/v1/spaces/{sid}/pinned-memories` 查看选择、实际容量和错误；`PUT` 同路径，JSON 数组传入 0–3 个当前空间事件 ID。此接口属于 AstrBot 适配层，独立服务不支持。`memory_plugin_events{stage="pinned"}` 记录每轮选择的常驻数量。


### 冻结事件

在「事件记忆」打开事件详情，点击「冻结事件」；需要继续修改时点击「解冻事件」。冻结与常驻独立，可以同时开启，也可以只冻结而不常驻，没有 3 件的冻结数量限制。

冻结后禁止自动追加、手工改写和合并/拆分，整理时新进展应另建事件关联原事件。常驻选择列表显示冻结状态。冻结不影响召回、遗忘权重和重提强化；已冻结的常驻正文不会因自动整理增长。未冻结的常驻仍按原容量策略处理。

### 复制与跨机器迁移

在内置网页侧栏「复制到其他空间」操作，本机复制保留源空间，双方之后独立使用。跨机器先导出 JSON，再到目标导入；导入前自动备份，重复事件可跳过或覆盖，冻结目标不能覆盖。

**迁移到新的聊天平台时，先发送一条消息生成目标聊天空间，再导入那个空间。** 同名空间不会自动绑定。模型配置、密钥与向量缓存不随文件迁移；目标需要重新配置，索引随后自动补建。

新导出包格式为 2，兼容旧版本 1 的完整包；已清理聊天正文无法从包中恢复。备份不自动删除。完整规则和停止服务后的恢复命令见 [数据迁移与恢复指南](https://github.com/YapAmbition/episodic-memory/blob/main/docs/data-management.md)。插件目录内提供 `restore_memory_backup.py`，请使用 AstrBot 的 Python 环境运行。

### Windows 启动时提示 unable to open database file

0.3.9 起，每条核心 SQLite 连接都设置 `PRAGMA temp_store=MEMORY`，排序与临时表使用内存，避免宿主继承的临时目录被删除后导致查询失败。数据库和 WAL 仍保存在原数据目录；大型排序会增加内存占用。

如果 AstrBot 由临时 Agent 环境启动，也建议在自己的 `run_astrbot.bat` 中、启动 AstrBot 的命令之前加入以下设置，避免其他组件继续继承短期临时目录（该脚本不属于本仓库）：

```bat
set "TEMP=%LOCALAPPDATA%\Temp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%"
```

这只设置当前启动进程及其子进程的环境变量，不修改系统全局环境。

0.3.10 修复导出错误被隐藏，以及强化来源消息被缓冲清理的问题。已缺失的强化来源在迁移包中保留 `unavailable_message_id` 和强化时间，不生成虚构消息；请用新版导入此类迁移包。其他缺失关联仍会提示具体字段，避免静默丢失来源。

0.3.11 让空间复制与文件迁移共用缺失强化来源的兼容处理，并修复事件合并将多个缺失来源强化记录错误去重的问题。

0.3.12 兼容缺失的消息回复对象：文件迁移和空间复制保留 `unavailable_in_reply_to` 原始 ID，不伪造消息、不改源库正文；缓冲清理也会保护尚存回复的祖先消息，避免再次产生悬空回复。


0.3.13 统一迁移补偿：文件导出、导入和本机空间复制共用字段补偿规则。缺失引用保留原 ID；必要身份、正文、时间或消费进度无法确认时，原记录进入恢复资料，不参与召回、整理或清理。页面预览和结果显示补偿／待恢复数量，可展开查看原始资料。源空间不改写；本机复制也在写入前创建 SQLite 备份。

新导出包为格式版本 2（需要本版或更新版导入），仍兼容读取版本 1 完整包。补偿前记录与历史原始快照随再次导出、复制一起保留，原始资料中的 ID 不会被重写。`GET /api/v1/spaces/{sid}/recovery?offset=0&limit=20` 分页只读查看，仍需控制台令牌。不自动恢复待恢复记录，不猜测角色、归属或消费进度。

0.3.14 修复迁移边界：复制会话标题限制为 100 字并保留原标题；恢复资料重复 ID 按来源分配稳定独立 ID；重复迁移仅在正文一致且相关事件结果到位时推进消费进度，不回退目标进度，完成后重新计算目标正文保留宽限期。发生写入错误时进度与事件一起回滚。


0.3.15 应急修复：AstrBot 默认从当前空间全部事件召回，避免跨平台迁移后旧人物关联导致事件被过滤。可开启 `recall_filter_by_focus` 恢复旧版仅关注人物召回；这可能排除已导入的其他人物事件。此开关不改变空间绑定、关注 UID、采集与整理规则，也不改写历史数据。升级后保留原关注 UID/平台配置，无需重新导入；未配置 embedding 时仍使用程序召回，语义匹配能力不等同于向量召回。`/记忆切片` 显示检索范围及当前人物关联事件数量。核心 API 新增 `filter_by_focus`，默认 true，其他接入保持原行为。

0.3.16 修复插件网页「找一件记得的事」遗漏召回范围参数：跟随插件的 `recall_filter_by_focus` 设置，默认检索当前空间全部事件，并显示生效范围。该入口仍为不调用 embedding 的程序检索。升级需要重新构建前端并刷新页面。

0.3.17：核心召回的人物过滤和加权，在当前空间内按非空 UID 精确匹配，忽略平台；没有 UID 则按内部人物 ID 匹配。相同 UID 的多条人物记录只计一次权重。适用于程序、向量和网页召回；空间绑定、采集、整理及存量人物/事件记录不变。`recall_filter_by_focus` 继续保留现有值（默认关闭）；开启后仅关注人物的过滤也能匹配迁入的同 UID 旧事件。无需再次导入或重建向量索引。

0.3.18：召回等待 embedding 期间若人物 UID、关注对象或事件关联发生变化，返回前重新校验人物身份和候选范围，防止旧候选绕过人物过滤；同时覆盖结构化召回、上下文与搜索接口。

### 插件诊断日志

插件自动将脱敏诊断写到 AstrBot 数据目录下的 `plugin_data/astrbot_plugin_memory_slice/logs/memory-slice.log`。每份约 5 MB，当前文件加 4 份轮转文件，最多约 25 MB；只轮转日志，不删除数据库或备份。

在插件配置中设置 `diagnostic_log_level`（默认 `INFO`；可选 `DEBUG`、`WARNING`、`ERROR`），重载插件生效。`DEBUG` 增加注入结果计数。日志记录整理起止、模型请求耗时/输入估算/候选数、整理异常、后台索引异常与召回降级，保存异常类型和调用栈位置，不保存异常原文、密钥、提示词、模型输出或聊天正文。

内置页面「运行监控 → 插件诊断日志」可查看最近 200 条、手动刷新和下载所有轮转日志。需要现有管理令牌；下载中包含内部空间和任务 ID。整理失败详情显示诊断编号，可在日志中定位。接口为 `GET /api/v1/diagnostics/logs`；加 `?download=true` 下载。日志写入失败不会阻断聊天，页面会提示检查磁盘及权限。升级前的错误无法补录。
