# 0.7.0：可嵌入记忆核心

0.6.0 提取公共核心；0.7.0 加入可选远程 embedding 并移除 LLM 联想召回。后续新增 [AstrBot 插件](../plugins/astrbot_plugin_memory_slice/README.md)，构建时打包同一份核心源码。

```text
backend/memory_slice/       共享核心（默认导入不依赖 Web 框架）
  service.py               MemoryService：应用操作、事务编排、整理调度和生命周期
  adapters.py              ModelAdapter 协议、RemoteModelAdapter 默认实现
  model.py                 兼容 Chat Completions 的远程调用及整理输出解析
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

## 召回与向量生命周期

`MemoryService.embeddings` 管理独立远程连接、内存密钥、向量索引和熔断。`service.start()` 启动索引任务，`close()` 取消并等待任务退出。该路径不使用宿主生成式模型适配器。

默认 local；hybrid 同时使用程序候选与 embedding 候选，随后沿用人物/遗忘/关联展开逻辑。向量文本包含标题、派生锚点及正文片段。事件正文按 1200 字符、1000 字符步长分片，批量最多 8 段；内容哈希和服务/模型/维度签名决定有效性。编辑或归档后旧片段不参与查询。查询失败保留程序结果并记录安全原因，向量故障不影响事件持久化。

SQLite 中保存 `embedding_config`（无密钥）和 `embedding_vector`；旧版 embedding 表/记录保留但不复用。当前采用精确余弦扫描，尚未做大规模 ANN 或压力验证。向量只负责语义相似度，不保证跨主题的经历结构联想。

旧保存的 llm/auto 默认值迁移为 local；显式 llm 请求拒绝。详情见 [接口](backend-api-design.md)。


### 请求上下文与记忆去重

核心 `/context` 输入新增可选 `current_context: string[]`，传入当前模型请求中实际存在的文本块（包括系统提示、历史、本轮输入和额外文本）。不传时保持原有召回行为；不要用永久会话历史代替实际请求。核心扫描全部文本检查完整记忆块，查询线索按近期字符预算选择，并排除协议记忆及规则块，不把全部上下文发送给 Embedding。

事件输出使用 `<memory-slice id="event_123" revision="2" supersedes="1">完整正文</memory-slice>`；revision 复用事件存储版本，修改事件会递增。正文 XML 转义。同 ID 在当前上下文已有同版或更新版本时跳过，旧版则输出最新完整记录，去重原因位于 `omitted`。旧 `<event>` 格式不会误判为新版协议。

规则使用 `<memory-slice-rules version="1">…</memory-slice-rules>`；有记忆输出且当前上下文缺少本版完整规则时附带。上下文裁剪后可再次注入，不维护会话永久已读状态。规则占用上下文字符预算。标签检测不是安全认证，接入方应传递真实请求内容。

AstrBot 插件从 ProviderRequest 传递当前可见文本，仍使用临时注入，不修改历史；后续框架裁剪或其他插件修改不在此快照内。状态命令为 `/记忆切片`，召回仅支持 local/hybrid；hybrid 可能增加聊天延迟，失败自动降级。
