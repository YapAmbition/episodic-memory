# 记忆切片 · Memory Slice for AstrBot

插件版本 **0.3.8**，内嵌 Memory Slice **0.7.0** 核心。无需另外部署 Docker 或 HTTP 服务。默认程序召回，可选远程 embedding。模型整理复用 AstrBot 已配置的聊天模型。

## 安装

要求 **AstrBot >= 4.24.0**，使用其内置 LLM/Agent 流程。该最低版本来自本轮临时内容块 API；不支持绕过版本限制安装到旧版。外接 Dify 等未触发标准请求钩子的执行器尚未适配。

1. 在 AstrBot 插件管理页面选择从本地 ZIP 安装，上传 `astrbot_plugin_memory_slice-v0.3.8.zip`。
2. 如果使用手工安装，将 ZIP 中的 `astrbot_plugin_memory_slice` 文件夹放到 AstrBot 的 `data/plugins/` 下；在 AstrBot 的 Python 环境安装该目录的 `requirements.txt`，然后重载插件。
3. 插件配置中选择已有聊天模型；留空时跟随当前会话。无需填写新的 API Key 或模型地址。
4. 私聊可以直接测试。群聊填写 `focus_uid`（要关注的人的平台 UID），建议同时填写 `focus_name` 和 `group_ids`。
5. 保存配置后重载插件，使当前实例和后台调度一起应用新配置。

首次安装依赖可能需要联网下载 Python 包。ZIP 包含记忆核心源码，不需要从 PyPI 下载一个未发布的 memory-slice 包。尚未发布到插件市场，`metadata.yaml` 的 repo 留空；本仓库的插件子目录不是可直接从 Git URL 安装的独立仓库，需要先打包。

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

构建产物保留 90 天（也受仓库/组织上限约束）。这个流程只生成下载产物，不创建 Release，不发布插件市场。需要长期对外提供下载时，可以把内层插件 ZIP 手动附到 GitHub Release；公开仓库的 Release 下载更方便。

不需要填写模型 API Key 或新增 GitHub Secrets，测试使用模拟宿主。GitHub 构建通过不代表 Windows + AstrBot 实机已经验收。

## 开发与测试

构建需要 Python 3.9+ 与 Node.js 20.19+ 或 22.12+；安装已打包 ZIP 的 AstrBot 主机不需要 Node.js。在 Memory Slice 项目根目录：

```bash
python3 scripts/build_astrbot_plugin.py
python3 -m unittest discover -s plugins/tests -v
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -q
```

构建脚本先在本机执行 React 生产构建（首次自动执行 `npm ci`），把页面静态资源放进 ZIP，再从 `backend/memory_slice/` 复制核心到 ZIP，生成 SHA-256 清单，避免手工维护两份业务逻辑。开发者修改核心后需重新构建插件；源码目录的相对 `.memory_slice` 在构建时补入。

默认输出在项目内的 `dist/astrbot_plugin_memory_slice-v0.3.8.zip`（可用 `--output` 指定位置）。已有与源码匹配的前端构建时可加 `--skip-frontend-build`；脚本校验源码指纹，过期资源会拒绝打包。

当前通过 25 项插件测试、113 项核心/HTTP 回归测试和 17 项前端工具测试。新增覆盖网页鉴权、同实例共享、只读配置、事件编辑、整理、端口释放及重启。插件测试使用真实共享核心、SQLite 和模拟 AstrBot 宿主，覆盖事件整理/召回、群聊筛选与隔离、重载、去重、超时取消、模型输出截断、临时注入、发送后记录、故障不阻断聊天及包内核心一致性。

**尚未在实际运行的 AstrBot + QQ 群环境联调，不将模拟测试等同于实机通过。** 首次验收建议使用测试群：配置关注对象，聊一件有独特细节的事，执行整理并确认完成，再用相关表达触发回忆；换另一个群确认不串记忆，重载后确认仍能召回。流式模式、@/回复和不唤醒机器人时的群消息到达情况也应一并验证。

接口依据：[AstrBot 消息钩子](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)、[模型接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[插件配置](https://docs.astrbot.app/dev/star/guides/plugin-config.html)、[数据目录](https://docs.astrbot.app/dev/star/guides/storage.html)。

## 可选远程向量召回（0.3.1）

在 AstrBot 插件配置设置 `retrieval_mode=hybrid`、`embedding_enabled=true`，填写 `embedding_base_url`（例如 `https://example.com/v1`）、`embedding_model` 和 `embedding_api_key`，保存并重载插件。调用的是该地址的 `/embeddings`，不复用聊天模型，不下载本地模型。

`embedding_timeout_seconds` 默认 1.5 秒；`embedding_failure_threshold` 默认 3 次，`embedding_cooldown_seconds` 默认 30 秒。超时、连接失败、401/403、429、服务器错误及错误向量响应均退回程序召回，不阻止聊天注入和回复保存。冷却后允许单个探测请求，成功自动恢复。

后台自动分片建立索引，事件编辑、更换模型后补建，未就绪时使用程序查找。`/记忆切片` 可查看索引进度和安全错误分类，管理网页的设置页只读展示连接状态，运行监控展示索引和熔断指标。群/私聊仍按原有空间隔离。旧 `llm` 配置升级为 `local`；生成式模型仅负责整理，召回不调用它。


### 请求上下文与记忆去重

旧兼容接口（当前插件不使用）：核心 `/context` 输入新增可选 `current_context: string[]`，传入当前模型请求中实际存在的文本块（包括系统提示、历史、本轮输入和额外文本）。不传时保持原有召回行为；不要用永久会话历史代替实际请求。核心扫描全部文本检查完整记忆块，查询线索按近期字符预算选择，并排除协议记忆及规则块，不把全部上下文发送给 Embedding。

事件输出使用 `<memory-slice id="event_123" revision="2" supersedes="1">完整正文</memory-slice>`；revision 复用事件存储版本，修改事件会递增。正文 XML 转义。同 ID 在当前上下文已有同版或更新版本时跳过，旧版则输出最新完整记录，去重原因位于 `omitted`。旧 `<event>` 格式不会误判为新版协议。

规则使用 `<memory-slice-rules version="1">…</memory-slice-rules>`；有记忆输出且当前上下文缺少本版完整规则时附带。上下文裁剪后可再次注入，不维护会话永久已读状态。规则占用上下文字符预算。标签检测不是安全认证，接入方应传递真实请求内容。

AstrBot 插件从 ProviderRequest 读取当前可见文本，在插件侧调用共用组装函数去重，仍使用临时注入，不修改历史；后续框架裁剪或其他插件修改不在此快照内。状态命令为 `/记忆切片`，召回仅支持 local/hybrid；hybrid 可能增加聊天延迟，失败自动降级。

已消费聊天原文默认在处理完成 30 天后由内嵌核心自动清理，事件记忆保留。可在内嵌前端「整理」设置中配置，或在高级记忆配置设置 `message_cleanup_enabled` 和 `processed_message_retention_days`。旧数据没有完成时间时从新版首次清理检查起算，不会升级后立即删除。


### 插件侧召回与去重

在 AstrBot 配置中设置 `recall_limit`（默认 8，范围 1–120）和 `context_char_budget`（默认 18000，范围 1000–100000 字符）。插件每次向核心结构化召回入口显式传入 `limit=recall_limit`；长度是插件注入预算，不是核心检索的过滤条件。

插件读取 ProviderRequest 当前实际可见的系统提示、全部历史、当前输入和额外文本，在本地调用共用组装函数完成事件/片段版本去重，再应用条数及字符预算，最后临时注入。规则和标签也计入字符数；完整同版记忆不重复注入，同版缺失片段可补充，新版会标明更新关系。不会把完整历史交给核心进行去重。只检查当前请求快照，不用永久“已发送”标记，记忆从上下文移除后可以再次注入。

去重后可能少于设置条数或为空，不会循环补查。未提供 ProviderRequest 时无法判断现有上下文，仍执行数量和长度限制。旧安装若没有这两个专用字段，兼容读取高级 JSON 的 `max_events`/`context_char_budget`；专用字段存在时优先采用专用字段。前文关于核心 `/context` 去重的说明仅适用于旧兼容接口，插件现使用 `retrieve` + 本地组装流程。


0.3.3：插件最终注入指标使用 `memory_plugin_injections_total`、`memory_plugin_events`、`memory_plugin_duration_seconds`；`/记忆切片` 展示最近一次结果。管理页面显示插件的每轮实际限制。近期线索按入库顺序限量读取，引用按 ID 查找。失败日志含阶段、错误分类、诊断编号及代码位置，不输出异常正文。


### 常驻事件（最多 3 件）

打开插件自带 Web 控制台，选择对应群聊/私聊空间，在「记忆设置 → 常驻记忆」勾选事件并保存；不需要重载插件。选择按空间存储，插件重启后保留。取消全部选择并保存即可关闭。

每次模型请求都完整注入所选事件的最新版本，不受相关性门槛和上下文去重影响。常驻最多 3 件，普通召回仍最多 `recall_limit` 件；常驻优先占用共同的 `context_char_budget`，普通召回使用剩余容量。同一事件不会在本轮常驻与普通召回中重复出现。常驻按 ID 直接读取；普通召回或拼接失败时仍提供有效常驻记忆，不向聊天窗口发送错误。降级记录在日志、失败指标与最近注入状态中，可主动通过 `/记忆切片` 查看。

保存时在同一写事务内读取最新事件、校验并保存，写锁最多等待 1 秒，不自动重试。数据库忙时保留原设置，设置页保留未保存的勾选并提供「重试保存」，不打扰聊天。保存时按实际输出校验容量，包含转义后的正文、标题、时间、状态、规则和标签。超过上限、超过 3 件、跨空间、已删除或已归档均拒绝保存，原选择不变。如果保存后事件增长或降低上限导致超限，暂停当轮记忆注入；聊天继续，`/记忆切片` 和选择页面提示调整，绝不静默截断常驻正文或突破上限。普通聊天需要正常经过 AstrBot 的模型请求钩子。

插件管理接口（沿用控制台认证）：`GET /api/v1/spaces/{sid}/pinned-memories` 查看选择、实际容量和错误；`PUT` 同路径，JSON 数组传入 0–3 个当前空间事件 ID。此接口属于 AstrBot 适配层，独立服务不支持。`memory_plugin_events{stage="pinned"}` 记录每轮选择的常驻数量。


### 冻结事件

在「事件记忆」打开事件详情，点击「冻结事件」；需要继续修改时点击「解冻事件」。冻结与常驻独立，可以同时开启，也可以只冻结而不常驻，没有 3 件的冻结数量限制。

冻结后禁止自动追加、手工改写和合并/拆分，整理时新进展应另建事件关联原事件。常驻选择列表显示冻结状态。冻结不影响召回、遗忘权重和重提强化；已冻结的常驻正文不会因自动整理增长。未冻结的常驻仍按原容量策略处理。

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

成功页面显示服务端完整备份路径，位于数据目录 `backups/before-import-*.sqlite3`。这是整库快照，包含其他空间；恢复会回到快照时刻。先停止独立后端（`./scripts/dev.sh stop`）或整个 AstrBot，保留导入后的新聊天，再运行：

```sh
python scripts/restore_memory_backup.py --backup "/备份路径/before-import-xxx.sqlite3" --database "/数据目录/memory.sqlite3" --service-stopped
```

使用安装了项目依赖的 Python。AstrBot 插件目录内也包含 `restore_memory_backup.py`，在插件目录运行时去掉命令中的 `scripts/`；使用 AstrBot 的 Python 环境。Windows 路径同样放在引号内。

恢复工具先获取与服务共享的跨进程互斥锁，运行中的新版服务会阻止恢复，恢复期间也不能启动服务。随后检查备份表结构、主键、空间配置及业务记录，校验通过才把当前数据库另存为 `backups/before-restore-*.sqlite3`，然后通过 SQLite 备份接口恢复并检查完整性。旧版服务尚无互斥锁，升级或恢复前仍须停止；不要在服务运行时执行。工具完成后再启动服务；如需撤回恢复，可以用刚保存的 before-restore 文件重复上述流程。
