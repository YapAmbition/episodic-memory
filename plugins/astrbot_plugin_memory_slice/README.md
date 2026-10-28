# 记忆切片 · Memory Slice for AstrBot

插件版本 **0.2.0**，内嵌 Memory Slice **0.6.0** 核心。无需另外部署 Docker、HTTP 服务或配置 embedding。模型整理复用 AstrBot 已配置的聊天模型。

## 安装

要求 **AstrBot >= 4.24.0**，使用其内置 LLM/Agent 流程。该最低版本来自本轮临时内容块 API；不支持绕过版本限制安装到旧版。外接 Dify 等未触发标准请求钩子的执行器尚未适配。

1. 在 AstrBot 插件管理页面选择从本地 ZIP 安装，上传 `astrbot_plugin_memory_slice-v0.2.0.zip`。
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
| retrieval_mode | local | local 程序多路召回；llm 显式使用模型联想 |
| auto_maintain | true | 开启后台定时整理 |
| interval_minutes | 60 | 整理间隔，1–10080 分钟 |
| model_timeout_seconds | 60 | 单次记忆模型调用超时 |
| context_window_tokens | 0 | 模型上下文容量；0 为未知，使用保守输入预算 |
| output_tokens | 4096 | 整理输出预留 |
| ignore_prefixes | / 等 | 忽略指令消息；本插件管理指令始终忽略 |
| memory_options | {} | 核心高级参数 JSON |

例如高级参数：

```json
{
  "half_life_days": 21,
  "activation_threshold": 0.26,
  "max_seeds": 3,
  "max_depth": 3,
  "context_char_budget": 8000,
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
- `/记忆状态`：查看当前空间事件数、待整理数、最近整理任务状态和安全错误信息。
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

端口冲突、缺少前端资源或令牌不合要求时，网页启动失败会写入 AstrBot 日志，也可用 `/记忆状态` 查看；记忆插件继续运行。重载/卸载插件会关闭网页并释放端口。关闭网页不会清空数据。

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

## 开发与测试

构建需要 Python 3.9+ 与 Node.js 20.19+ 或 22.12+；安装已打包 ZIP 的 AstrBot 主机不需要 Node.js。在 Memory Slice 项目根目录：

```bash
python3 scripts/build_astrbot_plugin.py
python3 -m unittest discover -s plugins/tests -v
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -q
```

构建脚本先在本机执行 React 生产构建（首次自动执行 `npm ci`），把页面静态资源放进 ZIP，再从 `backend/memory_slice/` 复制核心到 ZIP，生成 SHA-256 清单，避免手工维护两份业务逻辑。开发者修改核心后需重新构建插件；源码目录的相对 `.memory_slice` 在构建时补入。

默认输出在项目内的 `dist/astrbot_plugin_memory_slice-v0.2.0.zip`（可用 `--output` 指定位置）。已有与源码匹配的前端构建时可加 `--skip-frontend-build`；脚本校验源码指纹，过期资源会拒绝打包。

当前通过 25 项插件测试、113 项核心/HTTP 回归测试和 17 项前端工具测试。新增覆盖网页鉴权、同实例共享、只读配置、事件编辑、整理、端口释放及重启。插件测试使用真实共享核心、SQLite 和模拟 AstrBot 宿主，覆盖事件整理/召回、群聊筛选与隔离、重载、去重、超时取消、模型输出截断、临时注入、发送后记录、故障不阻断聊天及包内核心一致性。

**尚未在实际运行的 AstrBot + QQ 群环境联调，不将模拟测试等同于实机通过。** 首次验收建议使用测试群：配置关注对象，聊一件有独特细节的事，执行整理并确认完成，再用相关表达触发回忆；换另一个群确认不串记忆，重载后确认仍能召回。流式模式、@/回复和不唤醒机器人时的群消息到达情况也应一并验证。

接口依据：[AstrBot 消息钩子](https://docs.astrbot.app/dev/star/guides/listen-message-event.html)、[模型接口](https://docs.astrbot.app/dev/star/guides/ai.html)、[插件配置](https://docs.astrbot.app/dev/star/guides/plugin-config.html)、[数据目录](https://docs.astrbot.app/dev/star/guides/storage.html)。
