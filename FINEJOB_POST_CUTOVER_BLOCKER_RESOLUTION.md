# FineJob：Post-Cutover 三项阻断解除任务

## 任务目的

本任务只解决 Terra 在 `POST_CUTOVER_PATCH_BLOCKED` 中报告的三项阻断。

本任务不重新设计 Executor。
本任务不整仓回退。
本任务不推翻已经通过的最小补丁。
本任务不修改 MQTT/Techwolf 协议。
本任务不访问真实 BOSS。

本任务要解决的三项问题是：

```text
1. greeting 仍在旧实际执行链中，统一 Preflight 没有进入真实发送路径。
2. chat_message 没有真实接通“最新消息五分支”判断。
3. resume_send 的最新附件验证发生在 dispatch-started 之后，而不是 Preflight 之前。
```

---

# 一、当前已完成补丁必须保留

当前工作区已经完成并通过离线测试的内容必须保留：

```text
统一 Action 指定 action_id claim
聊天页不再直接 claim greeting
后端 claim 不再自行换领其他 Action
page_opened 后接入现有 loading
cooldown 后先 probe 当前页面
chat/resume 结果回报后进入现有 cooldown gate
独立消息 A/B waiting barrier 的后端基础
resume sender 发送前精确校验 encryptResumeId + filename
resume outbound observation 支持
greeting accepted 不再直接 succeeded
greeting 不再因为缺少唯一聊天账号绑定而 blocked
现有 session_sequence / unknown blocker
现有 sender 安全检查
```

Codex 不得为了处理本任务而撤销这些修复。

---

# 二、阻断 1：不要重写 greeting Executor，用“桥接”方式接入统一 Action

## 当前问题

当前真实 greeting 执行仍然由旧链驱动：

```text
fj_automation_actions
→ requestTaskPage
→ FineJob open_task_page
→ loading
→ page probe
→ encryptJobId match
→ legacy match_task
→ legacy sender
```

而统一 `fj_actions` 中对应的 greeting 只是同步 shadow。

因此：

```text
统一 greeting Preflight
```

没有进入真实发送链。

## 用户要求

用户不要重新实现这一整条旧链。

正确修复方式是：

**保留旧 greeting 队列作为“页面导航与页面匹配适配层”，但让对应 `fj_actions` 成为业务执行状态和 Preflight 的权威记录。**

## 必须优先复用 Stage 1 已有来源映射

Stage 1 已经存在类似：

```text
fj_actions.source_table
fj_actions.source_id
```

以及 legacy shadow sync / backfill。

Codex 必须先检查当前实际映射方式。

如果现有映射可以可靠从：

```text
legacy greeting task id
```

找到：

```text
对应统一 fj_action id
```

Codex 必须直接复用它。

不要新建第二套映射表。

---

# 三、greeting 正确桥接位置

旧页面链继续保持：

```text
requestTaskPage
→ FineJob open_task_page
→ 原 loading
→ probe
→ encryptJobId / URL 匹配
```

页面匹配成功以后：

```text
legacy greeting task 已明确
→ 根据 source mapping 找到唯一统一 greeting Action
→ 对这个明确的统一 Action ID 做 conditional claim
→ unified greeting Preflight
→ Preflight passed
→ 原 greeting sender
```

也就是说：

```text
页面导航：
继续使用旧链

页面匹配：
继续使用旧链

业务 claim / Preflight / canonical outcome：
使用对应 fj_actions
```

本任务不要求把：

```text
requestTaskPage
open_task_page
page probe
match window
sender
页面关闭
cooldown
```

重写成新 Executor。

---

# 四、legacy match_task 的处理原则

Codex 不得让 legacy `match_task` 和 unified claim 形成两个互相竞争的业务领取器。

Codex 必须检查最小改法。

优先方案：

```text
旧 match_task
只保留“这个页面已经匹配到这个 legacy navigation task”的作用
```

然后：

```text
立即解析对应 unified action
→ unified conditional claim
→ unified preflight
```

只有 unified claim + preflight 成功，才允许原 sender 继续。

如果 unified claim 失败：

```text
原 sender 不得执行
```

如果找不到唯一映射：

```text
fail closed
→ 不执行
→ 明确记录 mapping_missing
```

如果对应 unified Action 已经：

```text
succeeded
unknown
cancelled
superseded
stale
```

旧 legacy task 不得再次触发真实发送。

---

# 五、greeting 结果同步

原 sender 执行结束后：

```text
插件第一时间回报 FineJob
```

FineJob 必须同时保证：

```text
统一 fj_action canonical 状态更新
+
必要的 legacy 兼容状态同步
```

legacy 状态不能覆盖 unified canonical 结果。

统一 Action 是：

```text
业务结果权威
```

legacy 表只保留：

```text
导航兼容 / 历史兼容
```

---

# 六、greeting Preflight 必须真实进入 sender 前

真实顺序必须是：

```text
FineJob open page
→ loading
→ probe
→ 页面匹配
→ 找到对应 unified Action
→ unified claim
→ unified greeting Preflight
→ 原 sender 自身安全检查
→ side effect
```

greeting Preflight 至少确认：

```text
当前岗位 identity 与 Action 匹配
encryptJobId 匹配
岗位仍允许执行
没有同岗位 succeeded 的重复 greeting
没有同岗位 unknown 的 greeting
```

原 sender 继续检查：

```text
登录状态
页面 identity
encryptJobId
securityId
contacted == false
```

Preflight 不能替代 sender 最终检查。

---

# 七、阻断 2：chat_message 不要依赖调用方手工传 message_decisions

## 当前问题

当前调用方没有真实提供：

```text
message_decisions
replacement_text
independent_text
```

因此完整五分支没有接通。

## 修复原则

调用方只负责：

```text
获取最新聊天 delta
→ 上传 raw
→ 等待 semantic/state 更新完成
→ 把真实最新 conversation revision 交给 Preflight
```

调用方不应该自己编造：

```text
这条 B 和 A 相关
这条 B 和 A 独立
```

后端必须基于已经保存的真实消息和已有语义结果做决定。

---

# 八、chat Preflight 的真实输入

Preflight 必须能读取：

```text
原 Action 的 base_raw_message_id
base_message_mid
base_conversation_revision
base_cursor
coverage/link
当前 session 最新 raw messages
当前 semantic_type
reply_required
direction
semantic_context_json
当前真实 conversation revision
```

判断顺序继续遵守：

```text
结构字段
→ direction
→ 同 session Action / coverage 关联
→ 已有 semantic 结果
→ 必要时调用项目现有的回复规划/语义判断能力
```

Codex 不得通过全局字符串替换或简单“出现新 message id”来决定。

---

# 九、chat 五分支

## 1. 没有新消息

```text
A 的原 Reply-A
→ passed
```

## 2. 新 B 与 A 属于同一上下文

```text
A 不发送
→ A superseded
→ 使用项目现有回复生成/重规划能力生成 A'
→ A' 覆盖 A+B
```

如果 A 的原文本经过人工确认且 A' 文本改变：

```text
A' → awaiting_confirmation
```

## 3. B 是独立新问题

用户要求：

```text
A 保留
→ A 暂时不发送
→ B 进入现有回复/动作生成流程
→ 等待 B 对应动作真正 ready
→ 如果 B 需要人工确认，等待确认完成
→ A 与 B 都 ready
→ 释放 A
→ session_sequence 保证 A 先发送
→ B 后发送
```

这里的“一起发送”不是合并文本。

这里表示：

```text
两个动作都准备好以后
按业务顺序连续执行
```

当前已经做好的 A/B waiting barrier 可以保留并继续使用。

Codex 需要补的是：

```text
B 如何进入项目现有真实 action/reply planning 流程
```

不要创建假的 `independent_text` 参数让调用方填。

## 4. B 是无需回复的平台事件

如果现有 semantic 得出：

```text
reply_required=false
```

则：

```text
更新状态
→ A 继续
```

不得创建普通回复。

## 5. 无法可靠判断

```text
classification_uncertain
→ A waiting
→ 不 dispatch
```

---

# 十、必须复用项目现有回复生成流程

Codex 必须先找到项目当前真实用于：

```text
HR inbound
→ 生成 reply draft / action
```

的已有函数或服务。

对于独立 B：

```text
必须调用/复用这条已有流程
```

不要在 `action_scheduler.py` 中重新实现一套 AI 回复生成器。

如果现有回复生成流程无法安全从 Preflight 调用：

```text
Codex 必须把 B 放入已有“待规划/待生成”入口
```

并让 A 继续保持 waiting。

如果仓库当前确实没有任何可复用入口，Codex 才可以返回：

```text
POST_CUTOVER_BLOCKERS_STILL_BLOCKED
```

并给出缺失函数证据。

Codex 不得为了完成任务伪造回复文本。

---

# 十一、阻断 3：resume 最新附件验证必须发生在 dispatch-started 之前

## 当前问题

当前 sender 已经会在真实副作用前刷新附件并校验。

这个安全检查本身正确。

但是当前顺序是：

```text
dispatch_started
→ sender
→ sender 内刷新附件
→ 验证
→ side effect
```

用户要求执行前检查进入 Preflight。

## 正确顺序

必须改成：

```text
页面已匹配
→ unified claim
→ 插件执行只读 resume list refresh
→ 插件把最新附件 snapshot 交给 FineJob Preflight
→ 后端精确验证 Action 中保存的 encryptResumeId + filename
→ Preflight passed
→ dispatch_started
→ sender
→ sender 可继续保留最后一次防御性校验
→ side effect
```

---

# 十二、resume Preflight snapshot

Codex 应在现有 Preflight 请求/快照结构中最小增加：

```text
resume_list_snapshot
resume_list_observed_at
```

或使用当前代码已有等价字段。

snapshot 至少包含每份简历用于校验的：

```text
encryptResumeId
filename
```

不要把 PDF 正文放入 Preflight。

插件获取这个 snapshot 时：

```text
只能调用现有只读附件列表能力
不得产生 BOSS side effect
```

---

# 十三、后端 resume Preflight

后端收到最新 snapshot 后必须：

```text
找到 Action 已保存的 encryptResumeId + filename
```

如果精确存在：

```text
继续
```

如果不存在：

```text
Preflight 不通过
→ 不 dispatch
→ 不自动换简历
```

如果获取最新列表失败：

```text
fail closed
→ 不 dispatch
```

sender 中已有的发送前校验继续保留，作为最后一道防御。

---

# 十四、不要修改已通过的结果语义

当前补丁已经实现：

```text
resume transport accepted 不直接 succeeded
可信 outbound observation 可以完成 resume_send
greeting accepted 不直接 succeeded
```

本任务不得撤销。

当前：

```text
unknown 阻断同 HR 后续动作
```

也必须继续保留。

---

# 十五、必须新增的测试

所有测试必须离线。

## greeting bridge

必须覆盖：

```text
页面未匹配
→ unified greeting 不能 claim

页面匹配 legacy task
→ 能找到唯一 unified Action mapping
→ 只 claim 对应 Action

mapping 缺失
→ sender 不执行

unified Action 已 unknown/succeeded
→ legacy task 不得重复发送

unified Preflight fail
→ 原 sender 不执行

unified Preflight pass
→ 才进入原 sender
```

## chat 五分支

必须覆盖真实调用链，不只测 `_chat_preflight` 纯函数：

```text
delta ingest
→ semantic/state
→ preflight
```

至少测试：

```text
没有新消息
相关补充
独立新问题
无需回复平台事件
classification_uncertain
```

独立新问题必须验证：

```text
B 被送入现有 reply/action planning 流程
A 保持 waiting
B 未 ready → A 不 dispatch
B ready/必要确认完成
→ A 可执行
→ B 仍排在 A 后
```

## resume preflight order

测试必须断言：

```text
只读 resume refresh
→ backend preflight
→ preflight passed
→ dispatch_started
→ sender
```

并断言：

```text
resume refresh 失败/原 ID 不存在
→ dispatch_started 不发生
→ side effect 不发生
```

---

# 十六、本轮禁止事项

Codex 不得：

```text
重写 requestTaskPage
重写 open_task_page
重写 page probe
重写 URL/encryptJobId matcher
重写 sender
重写页面开关服务
重写 loading/cooldown
新增 account scheduler
新增 page kind matcher
新增 target_context UI matcher
新增 HR UI session 切换
重新研究 MQTT/Techwolf
访问真实 BOSS
```

---

# 十七、完成后的输出

第一行必须是：

```text
POST_CUTOVER_BLOCKERS_RESOLVED
```

或者：

```text
POST_CUTOVER_BLOCKERS_STILL_BLOCKED
```

然后继续完整输出。

每项必须写：

```text
阻断项：
修改文件：
修改函数：
修改前真实调用链：
修改后真实调用链：
为什么没有重写 Executor：
对应测试：
测试结果：
```

最后单独输出：

```text
当前仍保留的 legacy 兼容层：
当前 unified Action 的权威范围：
仍无法离线确认的真实平台行为：
```

---

# 十八、完成条件

只有以下三项全部满足，才能输出：

```text
POST_CUTOVER_BLOCKERS_RESOLVED
```

必须同时满足：

```text
1. greeting：
旧导航/页面匹配链保留
+ 页面匹配后桥接唯一 unified Action
+ unified claim
+ unified Preflight
+ 原 sender

2. chat：
最新 delta 后端真实完成五分支
+ 独立 B 进入已有 planning 流程
+ A 等待 B ready
+ A/B 按顺序发送

3. resume：
最新附件列表在 dispatch_started 之前进入 backend Preflight
+ sender 最终防御校验继续保留
```

如果任何一项没有真实接通，必须输出：

```text
POST_CUTOVER_BLOCKERS_STILL_BLOCKED
```

不能用“测试通过部分功能”替代完成条件。
