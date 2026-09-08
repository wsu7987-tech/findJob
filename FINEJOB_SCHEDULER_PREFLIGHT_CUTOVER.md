# FineJob BOSS 统一 Scheduler + Preflight Cutover 实施任务

## 背景

Stage 1 已通过定向复核：

```text
STAGE1_PASS
```

当前已经完成的基础能力包括：

- `greeting / chat_message / resume_send` 统一 Action 数据模型
- 旧 Action 回填与迁移期生命周期同步
- 实时消息 raw 保留
- message semantic / read_state / reply_state
- action-message coverage
- `human_takeover/manual` 不再作为人工发消息/发简历的硬阻断条件

本任务进入下一阶段：

```text
统一 Scheduler
+
统一 claim
+
执行前 Preflight
+
旧两套队列正式 cutover
```

本阶段**不要做大规模 UI 重构**，UI 只允许做为调试/兼容所需的最小调整。

---

# 目标架构

最终执行链统一为：

```text
greeting
chat_message
resume_send
      ↓
   fj_actions
      ↓
统一 Scheduler
      ↓
     claim
      ↓
  preflight
      ↓
 backend decision
      ↓
dispatch_started
      ↓
type-specific executor
      ↓
platform side effect
      ↓
result / reconciliation
      ↓
Action lifecycle / action log
```

旧的：

```text
fj_automation_actions 专用 greeting claim
fj_chat_send_actions 专用 chat/resume claim
```

不得继续作为新 Action 的权威调度入口。

---

# 一、统一 Scheduler

新增或完成统一调度服务，例如：

```text
backend/app/services/fine_job/action_scheduler.py
```

Scheduler 的权威数据源：

```text
fj_actions
```

支持：

```text
action_type:
- greeting
- chat_message
- resume_send
```

## Eligibility

Scheduler 只领取满足条件的 Action，至少考虑：

```text
status = queued
available_at <= now
没有有效 lease
不存在 unknown blocker
满足 session predecessor 顺序
必要 identity/page context 已准备
```

### Runtime 与 Action 创建分离

以下运行状态不得阻止用户创建/确认 Action：

```text
send_enabled=false
executor offline
leader unavailable
queue paused
```

这些状态应该让 Action 保持：

```text
queued
```

并设置：

```text
waiting_reason_code
waiting_reason_detail
waiting_since_at
```

真正 dispatch 时继续 fail-closed。

---

# 二、Priority + Page Affinity

默认业务 priority：

```text
HR 新回复产生的 chat_message       300
用户主动确认的 chat_message        300
resume_send                       200
greeting                          100
```

计算：

```text
effective_score =
    priority
    + page_affinity_bonus
    + waiting_age_bonus
```

建议：

```text
exact target_page_key     +20
same page kind/account     +8
other                       +0
```

```text
waiting_age_bonus = min(79, queued_minutes)
```

要求：

- 低业务 priority 不得仅靠 page affinity / waiting bonus 越过高业务 priority。
- 最终稳定排序至少包含：
  - effective_score DESC
  - queued_at ASC
  - id ASC

---

# 三、Page Affinity

保留当前执行器“尽量复用已打开页面”的优势，但不能让页面亲和性压过业务优先级。

页面 key 建议：

```text
chat_message / resume_send:
boss:{account_uid}:chat

greeting:
boss:{account_uid}:job:{encrypt_job_id}
```

Executor / backend runtime 状态需要能够报告：

```text
current_page_kind
current_page_key
current_context_key
current_action_id
same_page_batch_count
```

增加：

```text
max_same_page_batch = 8
```

语义：

- 连续执行 8 个相同 page key 后；
- 如果同一最高业务 priority 档位存在其他页面任务；
- 下一次 claim 必须考虑其他页面；
- 不允许 greeting 因该规则越过 chat_message / resume_send。

---

# 四、Session 顺序保证

同一个 BOSS chat session 的多个 `chat_message` 不允许乱序发送。

使用 Stage 1 已建立的：

```text
session_sequence
revision_no
supersedes_action_id
```

Scheduler eligibility 必须保证：

```text
同 session 的 sequence N+1
不能在 sequence N 尚未得到允许继续的终态前 dispatch
```

特别注意：

```text
unknown
```

必须阻断同 session 后续动作。

---

# 五、统一 Claim

目标：

```text
Scheduler select
→ 同一个写事务内 conditional claim
→ claimed + lease
```

必须避免：

```text
SELECT
→ 事务外 UPDATE
```

导致的并发窗口。

claim 至少更新：

```text
status = claimed
lease_owner
lease_expires_at
execution_epoch
attempt_count
updated_at
```

lease 超时可以重新进入 eligible，但：

- 不得生成新的业务内容
- 不得偷偷换 resume
- chat action 的原始 text / revision 不得变化
- 不得自动业务级 retry 一个结果 unknown 的 action

---

# 六、统一 Preflight

正式执行链改成：

```text
claim
→ preflighting
→ executor 获取最新页面/会话快照
→ backend preflight decision
→ dispatch token
→ dispatch_started
→ side effect
```

Preflight 通过后返回短期有效：

```text
dispatch_token
dispatch_deadline_at
```

如果 token 超时、会话 revision 再变化、运行条件失效：

```text
不得继续 dispatch
→ 重新 preflight
```

---

# 七、chat_message Preflight

Action 创建时已经保存：

```text
base_raw_message_id
base_message_mid
base_conversation_revision
base_cursor
planned_at
reply/coverage message ids
```

真正发送前：

```text
获取该 HR 最新聊天 delta
→ raw ingest
→ semantic classify
→ 更新 read/reply state
→ 与 Action 基线比较
```

## 决策规则

### A. 没有新消息

```text
原 Action preflight passed
→ dispatch
```

### B. 新消息与原回复上下文相关

```text
原 Action → superseded
创建 replacement Action A'
A' 覆盖 A+B
继承原 session_sequence
revision_no + 1
```

如果原 Action 文本曾由用户人工确认：

```text
replacement 文本变化后
→ awaiting_confirmation
```

原确认不得自动授权新文本。

### C. 新消息与 A 无关但需要回复

```text
保留 A
为 B 创建新的 chat_message Action
B 使用下一 session_sequence
A 先执行
B 后执行
```

不要因为 B 出现就一刀切取消 A。

### D. 平台事件 / 广告 / 无需回复

例如：

```text
resume_sent
resume_read_receipt
resume_viewed
resume_received_confirmation
platform_ad
platform_event_unknown
```

如果：

```text
reply_required=false
```

则：

```text
更新 semantic/read/reply/job activity
原 A 继续执行
```

不得创建普通 AI reply task。

---

# 八、chat 新消息判断

不要只靠是否出现新 message id。

实现明确 decision layer，例如：

```text
relevant_to_existing_reply
independent_reply_required
non_reply_platform_event
classification_uncertain
```

如果无法可靠判断：

```text
classification_uncertain
```

则 fail-closed：

```text
禁止直接 dispatch
→ 标记 waiting / needs_review / replan_required
```

不要猜测后自动发送。

---

# 九、resume_send Preflight

真正 dispatch 前至少检查：

```text
1. session / account identity 仍一致
2. HR 回复条件仍满足
3. action 固化的 encryptResumeId / filename 仍有效
4. 没有同 session 的重复 resume_send 已处于 succeeded / unknown
5. 没有相关平台事件使当前 action 失效
```

Action 创建后固定：

```text
resume_filename
encrypt_resume_id
```

Preflight 不允许：

```text
重新读取列表后自动换成另一份简历
```

如果原 resumeId 已失效：

```text
stale / blocked / needs_review
```

允许使用已有缓存；preflight 根据 freshness / 失效信号刷新列表，但不要求每次重新解析 PDF。

---

# 十、greeting Preflight

执行前复核：

```text
账号身份
岗位仍有效
encryptJobId 一致
securityId / 页面上下文
未已经沟通
不存在 succeeded / unknown 的重复 greeting
```

如果：

```text
contacted=true
```

则：

```text
superseded
reason=already_contacted
```

不得重复发送。

---

# 十一、Runtime Fail-Closed

保留现有 `send_enabled` 多层检查。

至少：

```text
claim eligibility / waiting reason
dispatch_started
真正 MAIN World side effect 前
```

都要检查。

但：

```text
send_enabled=false
```

不应该删除 Action，也不应该禁止用户创建/确认 Action。

只应：

```text
queued
waiting_reason=send_disabled
```

runtime 恢复后必须重新 preflight。

---

# 十二、统一 Executor 协议

boss-executor-extension 不再维护两套业务 claim 权威流程。

目标：

```text
claimUnifiedAction()
→ get preflight snapshot
→ submit preflight
→ receive dispatch decision/token
→ type-specific dispatch
```

type-specific handler：

```text
greeting
chat_message
resume_send
```

可以继续复用：

```text
default-greeting.ts
chat sender.ts
resume sender
MQTT/WebSocket/auth
chat leader tab
read-only probe
```

但 handler 只负责执行，不负责决定：

```text
下一任务是谁
业务 priority
是否需要审批
Action 是否过期
```

---

# 十三、旧队列 Cutover

Stage 1 已完成 legacy action backfill 与生命周期 shadow sync。

本阶段切换要求：

1. 新 Action 统一写 `fj_actions`
2. 新 executor claim 只从统一 Scheduler 领取
3. 旧 claim API 停止领取新动作
4. 旧表保留历史/兼容映射
5. 迁移中的旧 running/leased action 不得重复执行
6. 明确处理 queued / leased / dispatching / unknown

严禁：

```text
同一个 source action
旧 executor 执行一次
统一 executor 又执行一次
```

---

# 十四、Action 生命周期

本阶段统一实际使用：

```text
awaiting_confirmation
queued
claimed
preflighting
dispatching
accepted
succeeded

superseded
stale
blocked
cancelled
failed
unknown
```

注意：

```text
accepted != succeeded
```

MQTT transport accepted 继续保持保守语义。

只有可信 outbound observation / reconciliation 才允许升级到：

```text
succeeded
```

---

# 十五、Action Log / Event

至少产生结构化生命周期事件：

```text
action_created
action_confirmed
action_claimed
preflight_started
preflight_passed
preflight_replan_required
action_superseded
replacement_created
priority_preempted
page_switched
dispatch_started
transport_accepted
outbound_observed
succeeded
failed
unknown
cancelled
```

UI 暂时可以不全面展示，但数据必须准备好。

---

# 十六、必须测试

不要访问真实 BOSS。

至少覆盖：

## Scheduler

- 高 priority chat 越过低 priority greeting
- resume 越过 greeting
- 同 priority 按 queued_at/id 稳定排序
- page affinity bonus 生效
- page affinity 不能越过更高业务 priority
- waiting age bonus 生效
- max_same_page_batch 生效
- 不同账号隔离

## Session order

- 同 session sequence 1 未完成时 sequence 2 不可 claim
- sequence 1 `unknown` 阻断后继
- sequence 1 succeeded 后 sequence 2 可进入
- replacement 继承 sequence

## Claim

- 并发 claim 只能成功一个
- lease expiry 可重新领取
- unknown 不自动 retry
- source action 不重复执行

## chat preflight

- 无新消息 → pass
- 新相关消息 → superseded + replacement
- replacement 内容变化 → awaiting_confirmation
- 新无关但需回复消息 → 保留 A + 创建 B
- platform event → A 继续
- platform_ad → A 继续且不创建 reply
- classification uncertain → fail-closed
- historical refresh 与 realtime delta 都能触发 freshness 判断

## resume preflight

- HR 未回复 → blocked/waiting
- HR 已回复 → 继续
- resumeId 有效 → pass
- resumeId 失效 → stale/blocked
- 不会自动换另一份 resume
- duplicate succeeded/unknown resume_send 被阻止

## greeting preflight

- contacted=false → pass
- contacted=true → superseded
- identity mismatch → fail-closed

## Runtime

- send_enabled=false → Action 保持 queued
- runtime 恢复 → 重新 preflight
- dispatch 前关闭 → 不产生平台 side effect

## Dry-run

- no business HTTP side effect
- no MQTT publish
- no greeting side effect

---

# 十七、明确禁止

本阶段不要：

```text
大规模重构 BossChat.vue
重构 ReviewQueue.vue
重构 DeliveryRunStatus.vue
重构 AutomationLogs.vue
重新研究 MQTT / Techwolf
修改已通过 Native Trace 的发送协议
执行真实 BOSS 发消息
执行真实 BOSS 发简历
执行真实 BOSS 打招呼
```

允许 UI 只做：

```text
为了兼容新 API / 编译 / 调试所需的最小改动
```

---

# 十八、完成标准

必须满足：

```text
1. 新 greeting/chat_message/resume_send 统一由 fj_actions 调度
2. 旧 claim 不再领取新 Action
3. priority 生效
4. page affinity 生效
5. session 顺序有数据库级/调度级保证
6. claim 原子化
7. 三类 Action 都经过 preflight
8. chat 能区分新消息相关/无关/平台事件
9. resume 不会静默换附件
10. runtime 与 freshness 分离
11. unknown 阻断后继且不自动业务重试
12. Stage 1 semantic/read/reply/coverage 不被破坏
13. 定向测试通过
```

完成后输出：

```text
READY_FOR_UI_REFACTOR
```

如果存在 blocker：

```text
BLOCKED
```

并只列真正阻止 cutover 的问题。

不要重新扩大到协议审计或 UI 设计。
