# Memory Slice 开发指南

## 目标与阅读顺序

项目以事件保存共同经历，支持人物、时间、遗忘和关联召回。它提供记忆资料，不负责决定宿主怎样说话，也不追求通用任务执行能力。README 面向使用者；开发约束、测试和模块职责写在本文件及架构文档。

开始修改前检查 `git status`，保留用户已有改动。先读 [核心架构](docs/core-architecture.md)，按任务再读 [记忆机制](docs/memory-mechanisms.md)、[API](docs/backend-api-design.md)、[数据迁移](docs/data-management.md) 或 [AstrBot](plugins/astrbot_plugin_memory_slice/README.md)。历史测试报告位于 `docs/history/`，只作历史证据，不是现行规范。

## 代码边界

- `backend/memory_slice/` 是唯一公共核心源码。独立服务、Skill 服务包和 AstrBot 均复用它，不复制维护第二套业务逻辑。
- `MemoryService.retrieve` / HTTP `/recall` 返回结构化相关记忆，限制返回事件数量，不读取宿主完整上下文、不做历史去重。
- 接入层收集实际模型请求上下文，调用共享 `context_assembly.assemble_context` 去重和限制注入条数/字符数。AstrBot 在插件侧完成这一步。
- `MemoryService.recall` / `/context` 是保留兼容的一站式接口；不要因为名字相似误删或交换语义。
- 平台仍参与空间划分；召回的人物 UID 匹配不区分平台。不要把这一取舍扩展为跨空间检索、身份认证或注册映射规则。
- Codex 使用正式历史接口增量同步消息；Hook 只负责召回注入，不把原始 Hook prompt 当用户聊天入库。

## 必须保持的行为

1. **历史数据优先**：不清库、不以重新初始化解决升级故障。数据结构升级、导入、复制和恢复保留备份、版本检查与事务回滚；不可恢复的旧资料进入恢复区，不能悄悄丢弃。备份不自动删除。
2. 消息正文与事件分开。消息消费进度只能随成功写入原子推进；失败、中断、等待都不能误标已消费。清理正文保留消息身份、哈希和进度，重复同步不能复活已清理正文。
3. 整理必须有限调用、有限重试。当前每轮最多 8 次模型调用；失败或低进展时结束本轮；后续定时整理按原有日程独立触发。不要为了塞进输出预算把普通消息越切越碎。
4. embedding 可选，不自动安装本地模型。故障必须退回程序召回，不能使正常聊天失败；更换模型后不能混用不同签名的向量。
5. 冻结在核心写入处校验，不只靠提示词。常驻最多 3 件，独立于冻结；普通召回失败仍应尝试常驻注入，但不能突破总字符预算。
6. 注入去重以实际上下文的事件版本和片段为依据，不用永久“已读”标记。规则块有版本；资料里的指令不成为宿主指令。
7. 空间复制保留源空间，副本使用新全局 ID 和稳定来源标识，重复复制按来源协调；不按空间名称认同一空间。
8. 不泄露密钥、原始模型输出或真实聊天到日志、测试产物和发布包。真实模型评测需用户授权，默认用临时数据库和模型替身。

## 开发与验证

推荐 Python 3.12 和受 Vite 支持的 Node（20.19+ / 22.12+）。本机启动使用 `./scripts/dev.sh setup`、`start`、`restart-backend`、`status`、`stop`。React 开发模式热更新，后端改动后按需重启。不要擅自重启用户正在运行的服务、替换数据目录或清除内存密钥。

日常不使用 Docker，不因开发修改特地构建插件 ZIP。只在验证打包或用户要求发布时打包。发布与推送按用户授权执行。

以下命令在仓库根执行；测试应隔离于真实服务数据：

```bash
# 核心与 HTTP；临时根目录避免 app 导入时使用真实 data/
TEST_DATA_DIR=$(mktemp -d)
PYTHONPATH=backend MEMORY_DATA_DIR="$TEST_DATA_DIR" SEED_DEMO=false \
  .venv/bin/python -m unittest discover -s backend/tests -v

# AstrBot、打包及发布脚本测试（部分用例需要本机监听端口或前端构建产物）
.venv/bin/python -m unittest discover -s plugins/tests -v

# Skill 分发与 Codex 同步
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v

# 前端工具测试与生产编译
npm test --prefix frontend
npm run build --prefix frontend

# 无需真实模型的固定召回基线
.venv/bin/python scripts/evaluate_recall.py --compare evals/recall/baselines/local-v1.json
```

按改动选择测试：检索/片段/去重需固定召回及相关核心测试；插件注入需插件测试；迁移需复制、导入导出、补偿、恢复及旧数据兼容测试；同步需 `tests/test_codex_sync.py`。UI 布局需浏览器检查，单纯编译通过不代表视觉正确。文档改动检查链接、命令路径和与代码的一致性，不必跑整个模型/服务测试。

测试结果说明是否使用替身、是否真实宿主联调；不把历史报告中的数量当本轮测试结果。不要声称在本机未具备的 Windows / AstrBot 环境验收成功。

## 构建与文档维护

- 核心版本源：`setup.cfg`；插件版本源：`plugins/astrbot_plugin_memory_slice/metadata.yaml`；Skill 服务版本见构建脚本与生成清单。它们不是同一个版本号。
- 插件包由 `scripts/build_astrbot_plugin.py` 生成，包含同份核心和编译后的 React；不要编辑打包产物来修源码。
- `.github/workflows/build-astrbot-plugin.yml` 支持手工 ZIP 构建；`astrbot-v*` 标签触发发布仓库同步，普通 push 不发布。详见插件 README。
- 使用说明写 README 或专门操作指南；内部职责写架构文档。改变行为时更新对应唯一说明，避免把同一规范不断追加到多份文档。
- 已确认但尚未实现的改进集中记录在 [架构文档的已知问题](docs/core-architecture.md#已知问题与边界)，不能把计划写成既有功能。
