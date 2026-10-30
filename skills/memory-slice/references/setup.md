# 安装与开发说明

## 分发物

开发者运行仓库 `scripts/build_skill_bundle.py` 后获得：

- `memory-slice-service-0.2.1.zip`：独立版本的服务分发包，含现有核心、预构建 React 前端、托管入口和依赖清单。
- `memory-slice/` 与 `memory-slice-skill-0.2.1.zip`：轻量 Skill，含脚本、文档、版本和 SHA256 清单，不含服务代码。

服务分发版本独立于核心 API 显示版本。首版本地来源是 `release.json` 中相对 Skill 目录的路径，默认与 Skill 目录平级。把 Skill 复制到别处时使用 `setup --package "/绝对路径/memory-slice-service-0.2.1.zip"`。同一版本不允许替换包内容；发布变化时递增服务包版本并重新生成 Skill 清单。Mac/Linux 用 `cp`，Windows 用标准库文件复制。这里只复制程序包；首次建虚拟环境仍需联网从 PyPI 安装 Python 依赖。

安装者需要 Python 3.11+（含 venv/pip），不需要 Node/npm、Docker。开发者打包前端才需要 Node/npm。Mac 与 Linux 使用 `python3`，Windows 根据环境用 `python`。本版在 macOS 验证；其他平台尚需实际宿主验收。

## 安装位置和隔离

默认服务目录：

- macOS：`~/Library/Application Support/MemorySlice`
- Windows：`%LOCALAPPDATA%/MemorySlice`
- Linux：`$XDG_DATA_HOME/memory-slice`，未设变量时 `~/.local/share/memory-slice`

其中 `releases/` 存版本与各自虚拟环境，`data/` 存 SQLite，`config.json` 存端口、身份、空间映射和本地访问令牌，`logs/` 存日志。`--home` 可自定义隔离安装，后续命令必须传同一个目录。删除 Skill 不会删除这些数据。不要共享 config.json。

默认监听 `127.0.0.1:8095`，同端口提供 UI/API。`setup --port 8096` 可修改，已安装 Hook 每次读取配置，无需修改硬编码端口。`open` 将本地服务令牌通过 URL fragment 交给浏览器，前端保存后立即清除 fragment。服务不对公网监听。

## 密钥与维护

默认 `--credentials keyring`，模型和 embedding 密钥由操作系统密钥库保存。系统必须有可用的 keyring 后端；不自动退回明文存储。无可用密钥库时可以明确选择 `--credentials session`，但每次服务重启后要重新在页面填写密钥。页面原有“会话”字样仍沿用核心标签；实际持久化方式以安装状态的 credentials 为准。不要在命令行参数里传模型密钥。

初始空间未开启自动整理。配置模型并测试成功后，用户可在页面开启定时整理。仅记录消息不等于创建事件；召回只能返回已整理事件。

## 更新与 Hook

每次执行客户端业务命令都会检查 Skill 要求的版本。旧版本先准备新程序和依赖，再在服务空闲时停止、备份数据库、切换、健康检查；失败恢复旧配置和数据库。不删除旧版本和备份。数据迁移仍需真实升级验收，不能仅凭启动成功保证语义兼容。

Codex SessionStart 检测到新版本时会后台发起升级；Hook 不阻塞等待 pip 安装。升级期间可能暂时没有记忆注入，但聊天继续。可执行 setup 查看确定的升级结果。未知或不兼容 API 版本报错，不擅自降级。离线时已安装且兼容的版本可以正常拉起；初次安装或升级依赖下载失败会报错。

Hook 安装会保留其他定义，并在更改已有 hooks.json 前备份。CLI 版本检查是最低已适配版本门槛，不代表所有桌面/远程环境均支持。最终以宿主 `/hooks` 的识别与信任结果为准。移除时只删除 statusMessage 以 `Memory Slice — ` 开头的三个处理器，勿删整个 hooks.json。


## 开发者验证

在仓库根目录运行安装器单元测试和隔离冒烟测试。下面的测试安装不写入真实 Codex 配置，不调用模型；完成后测试进程会停止，测试数据保留在 `.run/`：

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_skill*.py' -v
.venv/bin/python dist/skill-local/memory-slice/scripts/client.py setup --agent codex --home .run/my-skill-test --port 18096 --credentials session --codex-home .run/my-skill-test-codex --no-sync
.venv/bin/python tests/smoke_skill.py --home .run/my-skill-test --skill dist/skill-local/memory-slice --test-upgrade
```

测试验证真实复制/安装、隔离 Hook 定义、消息去重和顺序、事件注入输出、不同会话共享空间、未知 Agent 共用空间、静态 UI、端口冲突、停止后由 Hook 拉起、升级和失败回滚。密钥持久化使用模拟密钥库单元测试，未对用户系统密钥库写入测试凭据；真实 Codex 信任与触发不在冒烟测试覆盖范围内。


## 0.2.0：Codex 后台增量同步

- setup 首次启用同步时写入永久起始时间；重启、升级、暂停后恢复均不重置。
- 每 15 秒只发现有更新的 `cli`/`vscode` 正常持久化会话，排除子 Agent、临时线程；正在进行的回合完成或中断后采集。不会把 Hook 原始输入入库。
- 按消息时间严格限定起点，仅用户文本和 `final_answer` 助手回复入库；多模态附件仍不采集。
- 一轮最多 100 个消息片段，每片段至多 20,000 字符；处理预算 15 秒（正在执行的接口调用最多另等 12 秒）。分页进度、去重 ID 与同步状态保存在记忆 SQLite 中；未知时间戳或接口不兼容显示错误，不擅自导入旧记录。
- `/api/v1/integrations/codex` GET 返回状态、进度、范围；PUT 配置 enabled、excluded_threads、excluded_projects（项目完整路径）、codex_executable。端点沿用本地服务令牌。
- POST `/api/v1/integrations/codex/poll` 请求尽快检查。POST `/api/v1/integrations/codex/backfill` 必须明确 thread_id、since、until，且只能补录正常用户会话。前端提供相同操作。
- 默认不清除旧版已写入的数据；历史错误记录不会因升级自动删除。旧会话若已有消息，补录会兼容旧 Hook 消息 ID，防止重复。
- 当前用户会话查看增加历史会话选择、最近消息排序及每 5 秒刷新；整理按钮仍限当前会话，并明确标注。
- Codex App Server 是只读连接：只 initialize/initialized、thread/list、thread/read、thread/turns/list、thread/items/list；不启动、恢复或修改 Codex 会话，不触发模型调用。远程宿主和其他 Agent 暂不通过这个采集器支持。
