# FineJob：Post-Cutover 一致性解阻实现任务

## 任务主要为了完成什么

本任务要一次性完成当前四个互相依赖的缺口，并把已经存在的局部补丁真正接成一条完整执行链。

本任务解决：

```text
1. chat 缺少“新消息 B 与待发送回复 A 的关系判定”真实入口。
2. 独立 B 需要进入现有回复规划流程，同时保留 A。
3. greeting 需要把旧页面执行链桥接到 unified Action 的 claim / Preflight / canonical result。
4. resume_send 需要一个独立的只读附件 snapshot 通道，并让 snapshot 在 dispatch_started 之前进入 backend Preflight。
```

这四项本轮允许一起实现。

本任务不要求 Terra 再把它们拆开等待。
本任务也不允许 Terra 因为四项存在依赖关系而只做路径分析、不写代码。

如果当前仓库真实接口与文档命名不同，Terra 可以做最小接口适配，但必须保持本文行为。

---

# 一、必须保留的当前工作区成果

Terra 不得回退已经完成并通过的补丁：

```text
指定 action_id claim
聊天页不能 claim greeting
后端 claim 不能换领其他 Action
page_opened 后进入现有 loading
cooldown 后先 probe 当前页面
chat/resume 结果回报后进入现有 cooldown gate
A/B waiting barrier 的基础状态
resume encryptResumeId + filename 固定校验
resume outbound observation
greeting accepted 不直接 succeeded
greeting 不再被唯一聊天账号绑定阻塞
session_sequence
unknown blocker
send_enabled fail-closed
dry-run 零真实 BOSS side effect
```

Terra 不得执行：

```text
git reset
git restore
git checkout 覆盖工作区
git clean
git stash
整仓回退
```

---

# 二、整体实现边界

本轮允许新增以下四个最小能力：

```text
A. message relation service
B. preserve-existing reply planning mode
C. greeting legacy→unified execution bridge identity
D. resume read-only snapshot command/result
```

本轮禁止新增：

```text
第二套 AI provider
第二套 Executor
新的页面生命周期
account scheduler
page kind matcher
target_context UI matcher
具体 HR UI session 切换
新的 loading 状态机
新的 cooldown 状态机
新的 MQTT/Techwolf 协议
```

---

# 三、A：新增项目级 message relation service

## 目标

系统需要判断：

```text
新消息 B 与当前待发送回复 A 的关系
```

只能输出：

```text
relevant_to_existing_reply
independent_reply_required
non_reply_platform_event
classification_uncertain
```

## 放置位置

Terra 必须把这个能力做成独立 service/function。

Terra 不得把 provider 调用直接写进 `action_scheduler.py`。

Terra 应优先放在现有 FineJob chat service 层，例如：

```text
backend/app/services/fine_job/message_relation.py
```

具体文件名可以按当前项目结构调整。

## provider 注入

Terra 必须复用项目现有：

```text
_post_json
run_codex_exec
或当前统一 AI provider/client
```

`action_scheduler.py` 不得自己创建 provider。

当前 router/service 已有 `AppConfig` 的地方必须把配置传入相关性判定入口。

如果 unified Preflight 当前拿不到 `AppConfig`：

```text
router
→ service orchestration
→ relation service
→ scheduler/state transition
```

Terra 可以为这条调用链增加最小参数传递。

---

# 四、message relation service 的确定性判断

Terra 必须先使用已有 Stage 1 数据：

```text
direction
semantic_type
reply_required
semantic_context_json
action-message links
base message ids
new message ids
conversation revision
```

如果最新消息已经明确是：

```text
reply_required = false
```

例如：

```text
resume_sent
resume_read_receipt
resume_viewed
resume_received_confirmation
platform_ad
已明确无需回复的平台事件
```

则直接返回：

```text
non_reply_platform_event
```

这类情况不调用 AI。

---

# 五、message relation service 的 AI 判断

只有真正的：

```text
direction = inbound
semantic_type = recruiter_text
reply_required = true
```

并且结构数据无法直接确定关系时，才允许调用现有 AI provider。

模型只执行分类，不生成最终回复。

模型必须返回严格结构化结果：

```json
{
  "decision": "relevant_to_existing_reply | independent_reply_required | classification_uncertain",
  "reason_code": "短稳定代码"
}
```

任何以下情况：

```text
provider 失败
timeout
JSON 非法
decision 非法
字段缺失
```

都必须返回：

```text
classification_uncertain
```

不得猜测后继续发送。

---

# 六、相关性判定必须可审计

Terra 必须复用现有 action event/log/context 记录：

```text
action_id
session_id
base message ids
new message ids
decision
reason_code
classifier/provider version
observed conversation revision
created_at
```

如果使用 AI：

```text
必须记录参与判断的 message ids
```

不要把完整自由文本推理保存成执行权威。

执行权威只能是结构化 decision。

---

# 七、B：给现有回复规划增加 preserve-existing 模式

## 当前问题

当前：

```text
_queue_reply_task
```

会让同 session 已有草稿/待确认 Action stale。

独立新问题 B 不能使用这个默认行为。

## 修复要求

Terra 必须给现有 reply planning 入口增加一个最小的显式模式。

例如：

```text
preserve_existing_pending = False
```

默认值必须保持旧行为。

只有：

```text
independent_reply_required
```

分支使用：

```text
preserve_existing_pending = True
```

该模式必须做到：

```text
保留 A
不把 A stale
为 B 创建新的 reply task / Action
给 B 分配 A 之后的 session_sequence
```

Terra 不得复制 `_generate_reply` 或另写一套回复生成器。

---

# 八、chat 真实五分支必须在服务层完成

真实链必须形成：

```text
最新 delta
→ raw ingest
→ semantic derive
→ 获取最新 conversation revision
→ relation service
→ chat Preflight decision
```

## 1. 没有新消息

```text
A passed
→ dispatch
```

## 2. B 与 A 相关

```text
A 不 dispatch
→ A superseded
→ 复用现有 reply planning/generation
→ 创建 replacement A'
```

A' 必须继承 A 的业务顺序位置。

如果 A 已人工确认且 A' 文本变化：

```text
A' → awaiting_confirmation
```

## 3. B 是独立新问题

必须执行：

```text
A 保留
→ A waiting
→ 使用 preserve_existing_pending=True 为 B 规划 Action
→ 等待 B ready
```

B ready 的定义：

```text
B 已完成生成
且已经进入 queued / 可执行状态
```

如果 B 需要人工确认：

```text
B 必须先完成确认
```

然后：

```text
A 解除 waiting
→ A 重新 Preflight
→ session_sequence 保证 A 先执行
→ B 后执行
```

这里的“一起发送”表示：

```text
A 与 B 都 ready 后再释放执行
```

不是合并成一条消息。

## 4. B 是无需回复平台事件

```text
更新 semantic/read/reply/activity
→ A 继续
```

## 5. 无法判断

```text
A waiting
→ classification_uncertain
→ 不 dispatch
```

---

# 九、chat orchestration 不要塞进 scheduler

`action_scheduler.py` 只负责：

```text
Action 状态
eligibility
claim
preflight state transition
session ordering
barrier
```

以下行为应放在已有 chat service / orchestration 层：

```text
最新 delta ingest
relation provider 调用
reply planning
replacement generation
independent B planning
```

这样 `action_scheduler.py` 不直接依赖 `AppConfig` 或 AI provider。

如果当前 endpoint 结构必须让 scheduler 接收最终 relation decision，可以传入已经由 service 层生成的结构化 decision。

不得再让插件调用方手工伪造 `message_decisions`。

---

# 十、C：新增 greeting legacy→unified execution bridge identity

## 目标

Terra 必须保留旧 greeting 页面链：

```text
fj_automation_actions
→ requestTaskPage
→ FineJob open_task_page
→ loading
→ probe
→ encryptJobId match
```

Terra 只在页面匹配成功后桥接统一 Action。

## 映射

必须复用：

```text
fj_actions.source_table = 'fj_automation_actions'
fj_actions.source_id = legacy_task_id
```

不得新建第二套映射表。

映射必须唯一。

如果：

```text
0 条
或
>1 条
```

则：

```text
fail closed
→ 不执行 sender
```

---

# 十一、greeting bridge execution identity

Terra 允许新增一个最小的 bridge identity，用于把统一 Action 的执行身份带到 sender 结果回报阶段。

例如：

```text
unified_action_id
unified_execution_epoch
unified_dispatch_token
```

具体字段名按现有协议风格。

bridge identity 必须与：

```text
legacy_task_id
legacy_execution_epoch
```

同时存在。

legacy identity 继续用于：

```text
旧页面导航/匹配兼容
```

unified identity 用于：

```text
unified claim
unified Preflight
unified canonical completion
```

---

# 十二、greeting bridge 的真实顺序

必须形成：

```text
legacy 页面匹配成功
→ 根据 source mapping 找到唯一 unified Action
→ 对明确 unified action_id conditional claim
→ unified greeting Preflight
→ Preflight passed
→ 生成 bridge execution identity
→ 原 greeting sender
→ sender result
→ 使用 bridge identity 更新 unified canonical result
→ 同步 legacy compatibility status
→ cooldown
```

如果 unified claim 失败：

```text
sender 不执行
```

如果 unified Preflight 失败：

```text
sender 不执行
```

如果 unified Action 已经：

```text
succeeded
unknown
cancelled
superseded
stale
```

legacy task 不得再次执行真实 greeting。

---

# 十三、MainWorldExecutionResult 的最小改动

当前结果只有 legacy task identity。

Terra 可以：

```text
给 MainWorldExecutionTask / Result 增加可选 unified bridge identity
```

或者使用当前已有 typed metadata 字段实现同等效果。

要求：

```text
不得改变 sender 业务逻辑
不得改变 MQTT/Techwolf
不得改变页面打开/关闭
```

sender 只需要透明携带 execution identity。

FineJob/backend 使用 unified identity 更新 `fj_actions`。

---

# 十四、D：新增 resume 只读 snapshot command/result

## 当前问题

最新附件读取只存在于 Main World：

```text
BossChatSender.listResumeAttachments
```

Background 没有独立只读结果通道。

## 修复要求

Terra 必须新增一个与真实发送结果完全分离的 command/result。

例如：

```text
BOSS_RESUME_SNAPSHOT_REQUEST
BOSS_RESUME_SNAPSHOT_RESULT
```

具体命名按项目规范。

必须带：

```text
request_id
action_id
```

结果只返回：

```text
encryptResumeId
filename
```

以及必要的：

```text
observed_at
error
```

不得返回 PDF 正文。

---

# 十五、resume snapshot 不能复用 completion result

Terra 不得复用：

```text
ChatSendExecutionResult
MainWorldExecutionResult 的“动作完成”语义
```

来传 snapshot。

snapshot 必须有独立类型。

Background 必须能够：

```text
发送 request
→ 等待对应 request_id result
→ timeout fail closed
```

snapshot timeout：

```text
不得 dispatch_started
不得 side effect
```

---

# 十六、resume 正确执行顺序

必须形成：

```text
unified Action 已页面匹配/可执行
→ claim
→ Background 请求只读 resume snapshot
→ Main World 调用现有 listResumeAttachments
→ snapshot result
→ Background 调用 backend Preflight，并提交 snapshot
→ backend 精确验证 Action 保存的 encryptResumeId + filename
→ Preflight passed
→ dispatch_started
→ 原 sender
→ sender 内最后一次防御性附件校验可以继续保留
→ side effect
```

以下情况全部 fail closed：

```text
snapshot timeout
snapshot error
原 encryptResumeId 不存在
filename 不匹配
```

系统不得自动换简历。

---

# 十七、不要回退当前结果语义

必须继续保持：

```text
transport accepted != succeeded
```

继续保持：

```text
resume_send 只有可信 outbound observation 才 succeeded
greeting accepted 不直接 succeeded
unknown 不自动 retry
unknown 阻断同 HR 后续动作
```

---

# 十八、本轮必须新增的离线测试

## A. relation service

必须覆盖：

```text
平台事件 → non_reply_platform_event
相关 recruiter_text → relevant_to_existing_reply
独立 recruiter_text → independent_reply_required
provider 非法输出 → classification_uncertain
provider timeout/error → classification_uncertain
```

AI provider 必须 mock。

不得访问真实模型。

## B. preserve-existing planning

必须验证：

```text
默认 _queue_reply_task 行为不变

independent B + preserve_existing_pending=True：
→ A 不 stale
→ B 创建
→ B sequence > A
→ A waiting
→ B 未 ready 时 A 不 dispatch
→ B queued/确认完成后 A 可重新通过
→ A 先于 B 执行
```

## C. chat 真实调用链

必须覆盖：

```text
delta ingest
→ semantic
→ relation service
→ planning/preflight
```

五种分支都要测。

不能只测 `_chat_preflight` 人工参数。

## D. greeting bridge

必须覆盖：

```text
legacy 页面匹配前 → unified claim 不发生
legacy 页面匹配成功 → 唯一映射
mapping missing → sender 不执行
mapping duplicated → sender 不执行
unified claim fail → sender 不执行
unified Preflight fail → sender 不执行
unified Preflight pass → sender 执行
result 带 bridge identity → unified canonical 更新
legacy compatibility 状态同步
unified unknown/succeeded → legacy 不重复执行
```

## E. resume snapshot

必须覆盖严格顺序：

```text
claim
→ snapshot request
→ snapshot result
→ backend Preflight
→ dispatch_started
→ sender
```

还必须覆盖：

```text
snapshot timeout → 无 dispatch_started
snapshot error → 无 dispatch_started
resume ID 缺失 → 无 dispatch_started
filename mismatch → 无 dispatch_started
不得自动选择其他简历
snapshot result 不得触发 action completion
```

---

# 十九、本轮完成后必须运行

至少运行当前相关后端与扩展测试：

```text
Stage 1 action store tests
action scheduler tests
boss chat service / router tests
finejob client tests
chat coordinator tests
chat sender tests
新增 relation tests
新增 greeting bridge tests
新增 resume snapshot tests
TypeScript typecheck
```

如果仓库有统一 lint/typecheck，运行与修改文件相关的现有检查。

禁止访问真实 BOSS。

---

# 二十、本轮输出格式

第一行必须输出：

```text
POST_CUTOVER_COHERENT_UNBLOCK_IMPLEMENTED
```

或者：

```text
POST_CUTOVER_COHERENT_UNBLOCK_BLOCKED
```

然后继续输出完整结果。

必须逐项写：

```text
A. message relation service
修改文件：
修改函数：
真实调用链：
测试：

B. preserve-existing planning
修改文件：
修改函数：
真实调用链：
测试：

C. greeting bridge identity
修改文件：
修改函数：
真实调用链：
测试：

D. resume snapshot channel
修改文件：
修改函数：
真实调用链：
测试：
```

最后写：

```text
当前 greeting 最终执行链：
当前 chat 独立 B 最终执行链：
当前 resume 最终 Preflight 顺序：
保留未改的旧执行器行为：
仍无法离线确认的平台行为：
```

---

# 二十一、什么时候才允许输出 IMPLEMENTED

以下四项必须全部真实完成：

```text
1. relation service 已实现并接入真实 chat Preflight。
2. independent B 已能保留 A 并进入现有 reply planning。
3. greeting 页面匹配后已真实桥接 unified claim/Preflight/result。
4. resume snapshot 已在 dispatch_started 之前进入 backend Preflight。
```

只完成其中一部分：

```text
必须输出 POST_CUTOVER_COHERENT_UNBLOCK_BLOCKED
```

但本轮 Terra 已经获得实现这四项的明确授权。

Terra 不应再因为“这四项必须一起实现”而只做分析不写代码。
