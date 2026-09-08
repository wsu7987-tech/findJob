# FineJob：Chat Relation 五分支端到端收口任务

## 任务目的

本任务只完成一件事：

**把当前已经写入工作区的 message relation service 和 preserve-existing planning 真正接成 chat_message 的完整端到端五分支，并补齐离线测试。**

本任务暂时不处理 greeting bridge。
本任务暂时不处理 resume snapshot。

原因：

```text
当前 chat 已经有部分代码写入工作区，
如果继续同时推进 greeting / resume，
会再次出现“三项互相等待、全部 BLOCKED”的情况。
```

所以本轮先把 chat 收口，确保已经写入的 relation / planning 代码不是半成品。

---

# 一、必须保留当前已完成内容

当前工作区已经存在：

```text
classify_pending_reply_delta
prepare_unified_chat_preflight
preserve_existing_pending=True
A waiting barrier
relation_audit
指定 action_id claim
真实最新 revision
session_sequence
unknown blocker
```

这些代码不要删除，不要重新设计。

本轮目标是把它们真正接通。

---

# 二、真实执行链必须形成

最终必须是：

```text
HR 新消息进入
→ raw ingest
→ derive_message_semantics
→ 找到当前 pending chat Action A
→ 读取 A 基线之后的新消息
→ classify_pending_reply_delta
→ 得到结构化 relation decision
→ prepare_unified_chat_preflight
→ 根据 decision 做五分支
→ Action 状态正确变化
→ 只有满足发送条件才允许 dispatch
```

插件不得手工传：

```text
message_decisions
replacement_text
independent_text
```

这些不能再作为真实调用链依赖。

---

# 三、五分支必须全部真实接通

## 1. 没有新消息

```text
A 保持原内容
→ Preflight passed
→ dispatch
```

必须有真实调用链测试证明。

---

## 2. 新消息 B 与 A 相关

当前问题：

```text
relation 已经能判断 relevant_to_existing_reply
但 replacement Action 还没有真正接入现有 reply generation
```

修复要求：

```text
A
→ relation = relevant_to_existing_reply
→ A 不 dispatch
→ A 标记 superseded
→ 调用项目现有 reply generation / planning
→ 使用 A 原 coverage + B 新消息生成 replacement A'
```

A' 必须：

```text
继承 A 的 session_sequence
revision_no + 1
建立 supersedes_action_id / 等价关联
覆盖 A 原消息 + B
```

如果 A 原内容曾人工确认，且 A' 文本发生变化：

```text
A' → awaiting_confirmation
```

旧确认不能自动授权 A'。

禁止：

```text
在 action_scheduler.py 内重新写一套 reply generator
```

必须复用现有：

```text
_queue_reply_task
_generate_reply
或当前项目真实 reply planning/generation 入口
```

---

# 四、相关 B 的 reply generation 必须有明确入口

Terra 必须找到当前真实的：

```text
reply task
→ generate
→ draft/action
```

调用链。

然后为 replacement 场景提供最小入口。

允许新增一个明确 mode，例如：

```text
replacement_for_action_id
covered_message_ids
preserve_session_sequence
```

具体名字按项目现有风格。

要求：

```text
默认普通 reply generation 行为不变
只有相关 B replacement 分支使用 replacement 模式
```

---

# 五、独立 B 分支必须真正完成 B → Action ready

当前已经实现：

```text
independent B
→ preserve_existing_pending=True
→ 新 B reply task
→ A waiting
```

但当前还缺：

```text
B reply task
→ 自动生成/确认
→ 统一 Action ready
```

本轮必须接通。

正确链：

```text
relation = independent_reply_required
→ A 保留
→ A waiting
→ 为 B 创建新的 reply task
→ 使用现有 reply generation 生成 B draft
```

然后：

### B 需要人工确认

```text
B draft/action → awaiting_confirmation
A 继续 waiting
```

用户确认 B 后：

```text
B Action → queued/ready
→ A barrier 解除
```

### B 属于允许自动执行

```text
B Action → queued/ready
→ A barrier 解除
```

然后：

```text
A 重新 Preflight
→ A passed
→ A 先执行
→ B 因 session_sequence 更晚，在 A 完成后执行
```

---

# 六、A 的 barrier 不能只看 reply task confirmed

当前实现把：

```text
B planning task confirmed
```

作为 A 的 waiting 条件。

Terra 必须检查真实数据链，最终 barrier 应该判断：

```text
B 已经存在对应统一 Action
且该 Action 已达到 ready / queued
```

如果 B 需要人工确认：

```text
确认前不算 ready
```

如果当前项目中 `confirmed` reply task 本身可靠等价于“对应 unified Action 已 queued”，可以复用。

如果不等价，必须用真实 unified Action 状态判断。

不能只看一个孤立 planning task 状态。

---

# 七、平台事件分支

如果最新新增消息已经由 semantic 层判断：

```text
reply_required = false
```

则：

```text
relation = non_reply_platform_event
→ 更新消息状态/活动
→ A 不被 stale
→ A Preflight passed
```

不得：

```text
调用 AI relation provider
创建普通 reply task
```

---

# 八、不确定分支

以下任何情况：

```text
provider error
provider timeout
非法结构
语义输入缺失
无法可靠判断 related / independent
```

必须：

```text
classification_uncertain
→ A waiting
→ 不 dispatch
```

不能自动重新生成。
不能自动发送。

---

# 九、relation audit 必须保持

当前：

```text
payload_json.relation_audit
```

已经记录：

```text
Action
session
基线 message
新 message
revision
decision
provider version
```

这部分继续保留。

如果本轮调整字段，必须保持可审计性。

不得保存模型隐藏推理。
只记录：

```text
结构化 decision
reason_code / 简短 summary
```

---

# 十、不得改变的其他执行逻辑

本轮禁止修改：

```text
greeting bridge
resume snapshot
页面 open/close
loading
cooldown
URL/encryptJobId matcher
MQTT/Techwolf
account scheduler
具体 HR UI session
```

本轮只处理 chat 五分支闭环。

---

# 十一、必须补齐的离线测试

## relation service 单测

必须单独测试：

```text
non_reply_platform_event
relevant_to_existing_reply
independent_reply_required
classification_uncertain
provider error
provider invalid output
```

provider 必须 mock。

---

## 真实五分支调用链测试

必须测试真实：

```text
raw ingest
→ semantic
→ relation
→ planning
→ preflight
```

不能只手工调用 `_chat_preflight(message_decisions=...)`。

至少覆盖：

### 无新消息

```text
A passed
```

### 相关 B

```text
A superseded
→ replacement reply planning 被调用
→ A' 创建
→ A' 继承 sequence
→ A' coverage 包含 A+B
```

### 相关 B + 原 A 人工确认

```text
A' 文本变化
→ awaiting_confirmation
```

### 独立 B

```text
A 不 stale
→ B reply task 创建
→ B unified Action 生成
→ A waiting
→ B 未 ready 时 A 不 dispatch
→ B ready 后 A 可重新 Preflight
→ A sequence < B
```

### 平台事件

```text
不调用 relation AI
→ 不创建 B reply
→ A passed
```

### uncertain

```text
A waiting
→ 不 dispatch
```

---

# 十二、必须检查旧默认行为不被破坏

测试必须验证：

```text
普通 _queue_reply_task 默认调用
```

仍然保持旧行为。

只有：

```text
independent_reply_required
```

使用 preserve-existing 模式。

只有：

```text
relevant_to_existing_reply
```

使用 replacement 模式。

---

# 十三、测试要求

至少运行：

```text
backend/tests/fine_job/test_action_scheduler.py
backend/tests/services/test_action_store_stage1.py
新增 message_relation tests
boss_chat reply planning 相关 tests
```

如果修改 router/service schema，运行对应 API/service tests。

禁止真实 BOSS。
禁止真实外部模型。
AI provider 全部 mock。

---

# 十四、完成输出

第一行必须输出：

```text
CHAT_RELATION_E2E_COMPLETE
```

或者：

```text
CHAT_RELATION_E2E_BLOCKED
```

然后继续输出：

```text
relation service：
replacement A'：
independent B planning：
A/B barrier：
平台事件：
uncertain：
测试结果：
```

每一项写：

```text
修改文件
修改函数
修改前行为
修改后行为
对应测试
```

---

# 十五、什么时候才能 COMPLETE

必须全部满足：

```text
relation service 已有独立单测
真实 chat 五分支已有端到端离线测试
相关 B 会真实生成 replacement A'
独立 B 会真实生成自己的 unified Action
A 会等待到 B Action ready
平台事件不会打断 A
uncertain 会 fail closed
真实调用链不再依赖插件手工 message_decisions
```

任何一项未完成：

```text
CHAT_RELATION_E2E_BLOCKED
```

本轮不允许因为 greeting / resume 尚未完成而返回 BLOCKED。
greeting / resume 不属于本轮完成条件。
