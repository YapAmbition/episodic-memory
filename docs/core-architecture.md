# 0.6.0：可嵌入记忆核心

0.6.0 提取公共核心并移除 embedding。后续新增 [AstrBot 插件](../plugins/astrbot_plugin_memory_slice/README.md)，构建时打包同一份核心源码。

```text
backend/memory_slice/       共享核心（默认导入不依赖 Web 框架）
  service.py               MemoryService：应用操作、事务编排、整理调度和生命周期
  adapters.py              ModelAdapter 协议、RemoteModelAdapter 默认实现
  model.py                 兼容 Chat Completions 的远程调用及联想输出校验
  store.py                 SQLite、全文索引、短连接事务
  engine.py / retrieval.py  评分、淡忘、入口检索和关联展开
  maintenance*.py          追加维护、预算、补读、分段与版本
  focus.py                 关注筛选和临时消息清理
  schemas.py / errors.py   入参约束及独立领域错误
  monitoring.py            每实例指标和快照
  web.py / web_middleware.py 可选 HTTP 适配，使用时需安装 server 依赖
backend/app/               独立 HTTP 入口
frontend/                  现有 React 管理界面
```

FastAPI 和未来插件都调用同一个 `MemoryService`。核心不创建 Web 应用、不监听端口、不依赖全局应用单例，默认也不导入示例数据；一个实例管理一个目录。请勿让两个活跃服务共享同一个数据库目录，调度锁仍为进程内锁。

## 不启动服务器的调用例子

安装核心：`pip install .`，无需安装 FastAPI；仓库开发也可以设置 `PYTHONPATH=backend`。独立 HTTP 服务继续使用原有 `backend/requirements.txt` 与启动命令。

```python
import asyncio
from memory_slice.service import MemoryService
from memory_slice.schemas import (
    SpaceInput, ConversationInput, MessageInput, ContextInput, MaintenanceInput,
)

async def main():
    memory = MemoryService("./my-memory-data")
    await memory.start()
    try:
        space = memory.add_space(SpaceInput(name="我的记忆"))
        sid = space["id"]
        person = memory.persons(sid)[0]
        conversation = memory.add_conversation(sid, ConversationInput(person_id=person["id"]))
        memory.add_message(sid, conversation["id"], MessageInput(
            role="user", content="昨天烤面包失败，叫它陨石一号。", client_id="message-1",
        ))
        job = memory.enqueue(sid, MaintenanceInput(conversation_id=conversation["id"]))
        while memory.get_job(sid, job["id"])["status"] in ("queued", "running"):
            await asyncio.sleep(.05)
        result = await memory.recall(sid, ContextInput(query="陨石一号"))
        print(result["context"])
    finally:
        await memory.close()

asyncio.run(main())
```

此例使用无模型原文归档；配置关注对象但没有整理模型时会暂缓。实际应用应保存并复用空间和会话 ID，不要每次启动重新创建。

## 宿主提供模型

`MemoryService(data_dir, model=adapter)` 接受实现以下异步方法的对象：

```python
async def complete(self, settings, api_key, messages,
                   json_mode=False, purpose="other", metrics=None,
                   max_tokens=None) -> str:
    ...
```

返回模型文本，不返回 HTTP Response。整理、查询扩展、关联判断及模拟聊天都会使用注入对象，不会绕回默认远程模型。宿主应尊重预算、超时和取消，输出截断或明确上下文超限分别抛出 `OutputLimitError`、`ContextLimitError`，不得把部分输出作为完整整理结果提交。

`settings` 仍使用现有模型配置，需将该空间设为 `mode="llm"` 并填写宿主模型标识。默认 `RemoteModelAdapter` 沿用现有密钥配置；未来插件可自行解析宿主模型，不需要单独填写远程地址或密钥。模型指标由适配器记录，缺少服务商 token 用量时不得编造。

## 生命周期和错误

- `start()` 在已有运行实例上幂等；恢复未完成整理并启动调度。
- `close()` 取消并等待后台任务，处理中任务恢复为待运行；清除内存密钥。SQLite 连接由各操作的上下文管理器关闭。
- 关闭后不能提交新任务，可显式 `start()` 重启。推荐每个宿主加载周期创建一个新实例。
- 核心抛 `MemoryError`，保留 `status_code`、`detail` 及可选 `headers`；HTTP 层映射为原有响应。业务代码无需捕获 FastAPI 异常。
- 核心日志使用 `memory_slice` logger，默认传播给宿主日志系统；错误详情继续排除原始模型输出和密钥。

## 移除 embedding 的兼容规则

- 默认 `local`，可选 `llm`。旧请求的 `auto` 是程序召回兼容别名，保存配置时归一化为 `local`。
- 旧数据库的 `auto` / `hybrid` 默认模式在初始化时迁移为 `local`；新的 `hybrid` 请求返回 422。
- 移除 `/embedding`、测试、状态、重建等接口、索引调度、向量召回和相关指标。能力接口不再返回 embedding。
- 旧向量表及配置只保留在数据库中作为历史资料，不读取、不使用、不自动删除；新库不再创建向量表。
- 未完成的旧索引任务标记失败且不恢复，尝试重试返回 410。事件、原始消息、正文、版本和关联保留。
- 前端移除语义配置、索引按钮及指标卡片，保留程序路径、模型联想、整理与关注指标。

原有事件记忆和召回行为通过迁移后的回归测试覆盖；新测试还在禁止导入 Web 框架的子进程中调用核心，确保其可以独立运行。

## AstrBot 内置网页（插件 0.2.0）

`memory_slice.web.create_app(service=runtime.core, host_mode="astrbot", access_token=..., frontend_dir=..., scope_info=...)` 复用宿主实例，不启动或关闭其核心。插件的 `WebServer` 管理监听 socket 和 Uvicorn 任务，不接管宿主信号。卸载时先关闭网页，再关闭核心。独立服务不传 `service`，继续由应用生命周期管理核心。

管理模式要求访问令牌；配置和模拟器相关写入返回 403，事件编辑和整理复用现有接口。不要再启动第二个后端操作插件数据库。
