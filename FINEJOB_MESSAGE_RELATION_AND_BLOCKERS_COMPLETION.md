# FineJob：消息相关性判定 + 三项阻断完成任务

## 任务目的

本任务主要完成两件事：

1. **新增一个项目级、可审计、可复用的“新消息与待发送回复的关系判定”能力。**
2. **基于这个能力，完成当前仍阻断的 chat / greeting / resume 三条真实执行链。**

本任务解决的核心问题是：

```text
chat：
系统现在知道“有新 HR 消息”，但不知道新消息 B
到底是 A 的补充、独立新问题、平台事件，还是无法判断。

greeting：
旧页面导航/匹配链仍在，但 unified Preflight 没有桥接到真实 sender 前。

resume：
最新附件验证已经存在，但发生在 dispatch_started 之后，
还没有进入真正的 Preflight。
```

本任务不重写 Executor。
本任务不整仓回退。
本任务不修改 MQTT/Techwolf。
本任务不新增账号调度、页面类型调度或具体 HR UI session 切换。

---

# 一、当前已经通过的补丁全部保留

以下内容不得回退：

```text
指定 action_id claim
聊天页不直接 claim greeting
后端不自行换领 Action
page_opened 后进入现有 loading
结果回报后进入现有 cooldown gate
cooldown 后先 probe 当前页
A/B waiting barrier 基础
resume encryptResumeId + filename 固定校验
resume outbound observation
greeting accepted 不直接 succeeded
greeting 不再被“必须先绑定唯一聊天账号”阻塞
session_sequence
unknown blocker
send_enabled fail-closed
dry-run
```

---

# 二、允许新增：项目级“消息相关性判定”能力

用户明确允许新增一个项目级、可审计的消息关系判断入口。

这个能力只负责判断：

```text
新消息 B 与当前待发送回复 A 的关系
```

它不负责直接发送消息。
它不负责页面控制。
它不负责生成最终回复文本。
它不负责调度下一页面。

建议使用一个独立、可复用的 service / function，不要把判断逻辑塞进 `action_scheduler.py`。

名称可以按项目现有命名风格确定，例如：

```text
classify_pending_reply_delta(...)
```

名称不是强制要求，行为是强制要求。

---

# 三、消息相关性判定的输入

输入必须来自当前已经保存的数据，不允许调用方伪造 decision。

至少使用：

```text
当前 pending chat Action
Action 的 base message / coverage
Action 生成时的 conversation revision
Action 当前 planned reply text（仅作为上下文，不作为唯一判断条件）

从 Action 基线之后新增的 raw inbound messages
这些消息的 direction
semantic_type
reply_required
semantic_context_json
message id / mid
```

如果 Stage 1 已经有 action-message links，优先复用。

不得只根据：

```text
“出现了新 message id”
```

直接判断旧回复失效。

---

# 四、判定顺序

判定必须按照以下顺序：

```text
1. 结构字段
2. direction
3. 已有 Stage 1 semantic
4. reply_required
5. Action / coverage / session 关联
6. 对真正 recruiter_text 做相关性判断
```

平台事件不需要调用 AI 判断。

例如已经被 semantic 确认：

```text
resume_sent
resume_read_receipt
resume_viewed
resume_received_confirmation
platform_ad
其他 reply_required=false 的平台事件
```

直接得到：

```text
non_reply_platform_event
```

---

# 五、真正 recruiter_text 的相关性判断

对于：

```text
direction = inbound
semantic_type = recruiter_text
reply_required = true
```

如果仅靠结构和已有 semantic 不能确定关系，允许调用项目现有 AI/model 基础设施做一个**只分类、不生成回复**的判断。

必须复用项目当前已经用于 AI 回复生成的模型/provider/client。

不得为此新增第二套 AI provider。

模型只允许返回以下四类之一：

```text
relevant_to_existing_reply
independent_reply_required
classification_uncertain
non_reply_platform_event
```

对于真实 recruiter_text，正常主要在前三类中选择。

---

# 六、相关性判断必须可审计

每一次判定必须能追溯。

优先复用现有 Action event/log、JSON context 或 semantic context。

除非现有数据结构完全无法承载，否则不要新建大表。

至少记录：

```text
action_id
session_id
base message ids
new message ids
decision
reason_code
classifier/version
observed conversation revision
created_at
```

如果使用 AI 判断，还要记录：

```text
输入使用了哪些 message ids
模型/分类器版本
结构化 decision
简短 reason code / reason summary
```

不得把“模型自由文本解释”当作执行权威。

真正执行权威只能是结构化 enum。

无法得到合法结构化结果：

```text
classification_uncertain
```

---

# 七、禁止使用脆弱字符串规则代替相关性判断

不得通过：

```text
关键词 contains
全局字符串替换
固定几个中文问句
message 长度
是否带问号
```

判断：

```text
相关
独立
```

Stage 1 已有的精确平台事件规则可以继续保留。

例如平台固定事件的 exact semantic 规则，不属于本限制。

---

# 八、chat_message 五分支必须进入真实 Preflight

完成相关性判定后，真实执行链必须是：

```text
最新 delta
→ raw ingest
→ semantic derive
→ 最新 conversation revision
→ message relation decision
→ backend Preflight
→ decision
```

## A. 没有新消息

```text
Reply-A
→ passed
→ dispatch
```

## B. 新 B 与 A 相关

```text
A 不发送
→ A superseded
→ 使用项目现有 reply planning / generation 能力
→ 基于 A + B 生成 replacement A'
```

A' 必须继承原业务顺序位置。

如果 A 原来经过人工确认，而 A' 文本发生变化：

```text
A' → awaiting_confirmation
```

旧确认不能授权新文本。

## C. B 是独立新问题

用户要求：

```text
A 保留
→ A 暂时不发送
→ B 进入自己的 reply/action planning
→ 等待 B 的动作真正 ready
→ 如果 B 需要人工确认，等待确认完成
→ A 和 B 都 ready
→ A 先发送
→ B 后发送
```

这里的“一起发送”表示：

```text
两边都准备好以后
按 session_sequence 连续进入执行
```

不是把两条内容合成一条。

## D. B 是无需回复平台事件

```text
更新 semantic/read/reply/activity
→ A 继续
```

不得生成普通 AI 回复。

## E. 无法可靠判断

```text
classification_uncertain
→ A waiting
→ 不 dispatch
```

---

# 九、必须增加“保留 A 的 B 规划入口”

当前 `_queue_reply_task` 会把同 session 已有草稿 stale，这不符合独立 B 的行为。

Codex 必须在现有 reply planning 流程上做最小扩展，使它能够：

```text
为独立 B 创建自己的 reply/action
同时保留 A
```

可以增加一个明确模式/参数，例如：

```text
preserve_existing_pending=True
```

具体命名按项目风格。

要求：

```text
默认旧入口行为不变
只有独立 B 分支显式使用“保留 A”模式
```

不要复制一整套新的 reply generator。

不要在 `action_scheduler.py` 中写新的 AI 回复生成器。

---

# 十、独立 B 的 ready 定义

A 什么时候可以解除 waiting，必须明确。

B 至少达到：

```text
已经生成自己的 Action
且 Action 不再处于“待生成”
```

如果 B 需要人工确认：

```text
必须确认完成
```

如果 B 是预授权/允许自动执行：

```text
进入 queued/可执行状态
```

此时：

```text
A 可重新 Preflight
→ barrier 解除
→ A 按更早 session_sequence 先执行
→ B 后执行
```

---

# 十一、greeting：允许实现最小 bridge

现在允许 Terra 完成 greeting bridge，但禁止重写旧页面链。

保留：

```text
fj_automation_actions
→ requestTaskPage
→ FineJob open_task_page
→ loading
→ probe
→ encryptJobId match
```

页面匹配成功后：

```text
legacy task id
→ 使用 Stage 1 source_table/source_id
→ 找到唯一对应 fj_actions greeting
→ 指定 unified action_id claim
→ unified greeting Preflight
→ 原 greeting sender
```

如果找不到唯一映射：

```text
fail closed
→ 不执行 sender
```

如果 unified Action 已经：

```text
succeeded
unknown
cancelled
superseded
stale
```

legacy task 不得触发真实发送。

旧队列只继续承担：

```text
页面导航 / 页面匹配兼容
```

`fj_actions` 承担：

```text
业务 claim
Preflight
canonical outcome
```

---

# 十二、greeting 真实执行顺序

必须形成：

```text
FineJob open page
→ loading
→ probe
→ legacy 页面匹配
→ 找到对应 unified Action
→ unified claim
→ unified greeting Preflight
→ 原 sender 安全检查
→ side effect
→ 立即 report FineJob
→ unified canonical outcome + legacy compatibility sync
→ cooldown
```

原 sender 的：

```text
登录
identity
encryptJobId
securityId
contacted == false
```

全部保留。

---

# 十三、resume：新增只读附件 snapshot 通道

当前 Main World 已有：

```text
BossChatSender.listResumeAttachments
```

但 Background → Main World 没有“只读附件 snapshot 返回 Preflight”的独立通道。

用户允许新增这个**只读通道**。

这个通道只允许：

```text
请求最新附件列表
→ 返回 encryptResumeId + filename
```

不得：

```text
发送简历
改变 Action 状态为完成
复用真实发送结果通道伪装 snapshot
```

---

# 十四、resume 正确 Preflight 顺序

必须改成：

```text
页面已经匹配
→ unified claim
→ Background 请求 Main World 只读 resume snapshot
→ Main World 调用现有 listResumeAttachments
→ snapshot 返回 Background
→ Background 提交 backend Preflight
→ backend 精确验证 Action 已保存 encryptResumeId + filename
→ passed
→ dispatch_started
→ sender
```

如果 snapshot 获取失败：

```text
不 dispatch
→ fail closed
```

如果原简历不存在：

```text
不 dispatch
→ 不自动换简历
```

sender 当前已经存在的最后一次附件校验继续保留。

---

# 十五、结果语义不要回退

继续保持：

```text
transport accepted != succeeded
```

继续保持：

```text
resume_send 可信 outbound observation 才能 succeeded
greeting accepted 不直接 succeeded
unknown 不自动业务重试
unknown 阻断同 HR 后续动作
```

---

# 十六、本轮必须补的离线测试

## 消息相关性 service

测试：

```text
平台事件 → non_reply_platform_event
明显相关 recruiter_text fixture → relevant_to_existing_reply
明显独立 recruiter_text fixture → independent_reply_required
模型返回非法结构 → classification_uncertain
模型调用异常 → classification_uncertain
```

测试不得访问真实外部模型。

必须 mock 项目现有 AI provider。

## chat 真实调用链

测试：

```text
raw delta ingest
→ semantic
→ relation decision
→ preflight
```

覆盖：

```text
无新消息
相关 B
独立 B
平台事件
uncertain
```

独立 B 必须验证：

```text
A 没有 stale
B 被创建
A waiting
B 未 ready → A 不 dispatch
B ready / 必要确认完成
→ A 先执行
→ B 后执行
```

## greeting bridge

测试：

```text
页面未匹配 → 不 unified claim
legacy match → 精确找到 shadow Action
mapping missing → sender 不执行
unified Preflight fail → sender 不执行
unified Preflight pass → 原 sender 执行
unknown/succeeded unified Action → legacy 不重复发送
```

## resume snapshot

测试：

```text
snapshot request 是只读命令
snapshot 返回后才 backend Preflight
Preflight passed 后才 dispatch_started
snapshot 失败 → dispatch_started 不发生
ID/filename 不存在 → dispatch_started 不发生
不得自动换简历
```

---

# 十七、本轮禁止事项

不得：

```text
重写 Executor
重写 requestTaskPage
重写 open_task_page
重写 loading/cooldown
重写 URL/encryptJobId probe
新增账号 scheduler
新增 page kind matcher
新增 target_context UI matcher
新增具体 HR UI session 切换
新增第二套 AI provider
重新分析 MQTT/Techwolf
访问真实 BOSS
发送真实消息
发送真实简历
执行真实 greeting
```

---

# 十八、完成输出

完成后第一行输出：

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
修改目的：
修改文件：
修改函数：
修改前真实调用链：
修改后真实调用链：
为什么这是最小改动：
对应测试：
测试结果：
```

最后单独输出：

```text
消息相关性判定实际入口：
独立 B 的 planning 入口：
greeting legacy→unified 映射入口：
resume snapshot 只读通道：
仍无法离线确认的真实平台行为：
```

---

# 十九、完成条件

只有以下全部真实接通，才能输出：

```text
POST_CUTOVER_BLOCKERS_RESOLVED
```

必须同时满足：

```text
chat：
真实 raw/semantic → 项目级相关性判定 → 五分支
独立 B 可以保留 A 并进入现有 planning
A 等 B ready 后再按 A→B 执行

greeting：
旧导航/页面匹配链不重写
页面匹配后通过 source mapping 找到唯一 unified Action
unified claim + Preflight 真正在 sender 前执行

resume：
只读最新附件 snapshot 在 dispatch_started 之前进入 backend Preflight
sender 最终校验继续保留
```

任何一项没有真实接通：

```text
POST_CUTOVER_BLOCKERS_STILL_BLOCKED
```
