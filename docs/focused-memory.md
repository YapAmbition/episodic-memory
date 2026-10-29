# 单人关注与群聊接入

每个记忆空间可以配置一个关注对象。未配置时沿用通用整理，关闭人物召回通道，人物分不参与归一化，整理候选也不因人物匹配获得加分。无需额外身份服务；远程 embedding 为可选功能，查询沿用关注对象过滤。

## 接入步骤

以下路径均以 `/api/v1/spaces/{sid}` 为前缀，身份 ID 和消息 ID 使用服务实际返回值。

1. `POST /persons` 注册人物：`{"name":"张三","aliases":["小张"],"platform":"wechat","external_uid":"100"}`。平台与 UID 必须成对填写；同一空间内这对值唯一，重名允许。`GET /persons` 查询映射，`PUT /persons/{pid}` 更新。发送者也需注册。
2. `GET /config` 读取当前配置，修改 `focus_person_id` 为关注对象的内部人物 ID，再通过 `PUT /config` 保存完整配置。清空为 `null` 即通用模式。`focus_buffer_hours` 默认 24，范围 1–168；`focus_buffer_limit` 默认 2000，范围 200–20000。
3. `POST /conversations` 创建群会话：`{"kind":"group","title":"朋友群"}`。私聊 `kind:"direct"` 可沿用原 `person_id`；群聊不可用单一会话人物代替每条消息的发送者。
4. `POST /conversations/{cid}/messages` 发送原始消息，例如：

```json
{
  "role": "user",
  "sender_id": "person_li",
  "content": "是你把温度调高了，下次我帮你看看",
  "in_reply_to": "msg_zhang_previous",
  "mention_ids": ["person_zhang"],
  "client_id": "platform-message-123"
}
```

`in_reply_to` 是本服务返回的消息 ID，必须属于同一会话。`sender_id`、`mention_ids` 均为本服务人物 ID，不直接传平台 UID。接入方负责维护平台 ID 映射。同一 `client_id` 重试须保持正文、身份、回复和提及一致，否则返回 409。暂存缓冲满返回 429，接入方应保留待发送消息并重试。

5. 按配置的时机调用 `POST /maintenance`，例如 `{"conversation_id":"conv_id"}`；异步查询 `GET /jobs/{job_id}`。关注模式需要配置整理模型，不新增独立分类模型调用；沿用现有预算、补读与缩批机制，因此一次完整任务仍可能调用模型多次。
6. 调用 `POST /context` 获取可注入上下文：

```json
{
  "query": "这次面包又没成功",
  "person_id": "person_li",
  "reply_to_person_id": "person_zhang",
  "mention_ids": ["person_zhang"],
  "trace": true
}
```

`person_id` 表示当前发言者，可省略；回复对象与 @ 仅是召回情境，不是群成员清单。结果 `person_context` 解释人物权重是否启用、关注谁、使用了哪些交流对象。未配置关注对象时这些字段不产生人物加分。`/chat` 支持与消息接口相同的 sender/reply/mention 字段，但会生成助手回复；已有聊天 agent 通常使用 `/messages` 和 `/context` 分开接入。

## 记录规则

- 程序按发送者、明确提及、回复关系、无歧义名字挑选候选，同时带入有限的相邻消息；候选不代表一定相关。
- 模型判断事件是否与关注对象有关。事件必须包含关注人物，并提供本轮原话证据 `focus_evidence:{message_id,quote}`。缺少或伪造证据会使本批写入失败，原消息保留。
- 正文区分谁参与、谁转述、谁补充；这版使用人物列表和正文表达关系，没有另建角色边类型。模型判断仍可能误判，人工可检查来源并修改。
- 回复旧消息会携带最多 2000 字只读回复上下文，计入输入预算；截断有明确标记。关系不确定可暂缓，无关消息可忽略。
- 未配置整理模型时，候选暂缓，不把本地原文归档冒充相关事件。无关候选不会请求模型。
- 新消息先进入临时缓冲；相关事件及其来源长期保留。其他临时原文按实际接收时间到期清理，实验时间不会改变保留期限。关联来源、版本引用、未完成片段和回复依赖受到保护，实际保留数可超过临时上限。
- 旧记录不自动删除。关注模式召回、整理候选只包含涉及当前关注对象的旧事件；管理页仍可看到历史事件。更换关注对象不会自动回填或重写旧事件。关闭关注后，新消息永久保存，之前暂存消息仍按原到期时间处理。
- 本规则约束自动整理；手工创建/导入事件属于显式管理，不自动删除与关注对象无关的历史资料。

## 页面与监控

「记忆设置 → 关注对象」配置人物与缓冲；「对话实验室 → 新建群聊试验」可切换发送者、引用回复、选择 @。模型判定后的任务详情显示检查、候选、实际提交、保留来源、忽略和暂缓数量。

`GET /metrics` 和 `GET /api/v1/monitoring` 新增 `memory_focus_messages_total{outcome="scanned|candidates|retained|ignored|deferred|expired"}`。这些为进程内累计次数，重复检查会重复计数，非去重人数或唯一消息量；无用户 ID 指标标签。临时召回诊断也会过期，历史追踪按钮可能不再有记录。

这版是单空间单关注对象，尚未实现多关注对象策略、自动发现平台身份、跨平台身份合并。中文姓名识别是候选规则，接入方提供 UID 映射和结构化回复/@ 能减少误判与漏检。
