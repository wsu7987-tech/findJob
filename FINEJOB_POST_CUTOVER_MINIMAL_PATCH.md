# FineJob：Post-Cutover 最小修复任务

## 任务目的

本任务主要完成一件事：

**修复 `FINEJOB_SCHEDULER_PREFLIGHT_CUTOVER` 接入后已经确认的执行链回归，同时保留当前工作区已经正确完成的统一 Action、优先顺序、消息语义、固定简历校验和 `unknown` 阻断能力。**

本任务解决以下实际问题：

1. 统一 Action 在页面匹配之前被 claim。
2. 已存在的前置 loading 函数没有接入真实调用链。
3. chat Preflight 没有使用真实最新会话状态，也没有完成正确的新消息分支。
4. chat/resume 绕过原执行后 cooldown。
5. 页面保留时，cooldown 后没有先重新匹配当前页面。
6. greeting 的统一 Preflight 没有进入真实 greeting 链，并被错误的账号前置条件阻塞。
7. resume_send 执行前只验证旧缓存，且没有成功校正路径。
8. greeting 仍存在 `accepted` 被直接当作 `succeeded` 的旧状态语义问题。

本任务不重做整个执行器。
本任务不整仓回退。
本任务不修改 MQTT/Techwolf 已验证协议。
本任务不新增账号调度、页面类型调度或具体 HR UI 会话切换。

---

# 一、当前 Git 状态与保护要求

当前所有 cutover 改动仍在本地工作区，尚未提交。

Codex 必须：

```text
保留当前工作区
直接在当前工作区做最小修复
```

Codex 不得执行：

```text
git reset
git restore
git checkout 覆盖工作区
git clean
git stash
整仓回退
```

如果 Codex 需要参考 HEAD 原实现，Codex 只能只读查看：

```text
git show HEAD:<path>
git diff HEAD
```

Codex 不得覆盖当前文件。

---

# 二、必须保留的现有正确内容

Codex 不得删除或重做以下内容：

```text
fj_actions 统一 Action 数据体系
greeting / chat_message / resume_send 三类真实外发动作
FineJob 的业务优先顺序
raw message 保存
消息 semantic/state 分层
平台事件不自动生成普通回复
encryptResumeId + filename 固定简历校验
同 HR 会话的 session_sequence 基础
unknown 阻断后续同 HR 外发动作
send_enabled fail-closed
dry-run 零真实 BOSS 副作用
现有 greeting sender 的登录、identity、securityId、contacted 安全检查
现有 URL / encryptJobId / read-only probe 页面识别
FineJob 实际打开/关闭浏览器页面的服务
MQTT/Techwolf 发送协议
```

`account_uid` 等内部字段如果仍被 chat sender/auth 安全校验需要，可以保留为局部实现字段。

Codex 不得把它提升成：

```text
全局账号调度
账号 affinity
greeting 领取前置条件
页面 key 设计核心
```

---

# 三、最终执行顺序

修复后必须形成：

```text
FineJob 维护并同步最新有序 eligible 队列
→ 插件收到队列

如果当前页面刚由 FineJob 打开：
→ 完成原前置 loading
→ loading 完成后才 probe 当前页面

插件拿当前页面匹配最新执行队列
→ 当前页面能匹配任务
→ 从匹配结果中选择 FineJob 排序最靠前的 Action
→ 携带明确 Action ID 请求 FineJob lock/claim
→ lock/claim 成功
→ Preflight
→ dispatch
→ sender 执行真实动作
→ 插件第一时间报告 FineJob
→ FineJob 更新 Action/队列
→ 原执行后 cooldown/loading

cooldown/loading 完成：
→ 如果当前页面仍存在，先重新 probe/match 当前页面
→ 当前页面仍有匹配任务：继续
→ 当前页面没有匹配任务：插件请求 FineJob 打开下一目标页面
→ FineJob 实际打开
→ 前置 loading
→ 再匹配
```

禁止出现：

```text
claim
→ 找页面
→ 开页面
```

禁止出现：

```text
page_opened
→ 立即 probe
```

禁止出现：

```text
执行完成
→ 立即再次 claim
```

---

# 四、修复 1：统一 Action 必须先页面匹配，再 claim

## 当前错误

当前 `chat-coordinator.ts` 会在聊天 heartbeat 后直接调用统一 `/actions/claim`。

后端会在没有真实 page probe/URL/任务 identity 匹配的情况下，从三类 Action 中领取任务。

这导致：

```text
统一 greeting 可以在聊天页被 claim
统一队列绕过原岗位页匹配
current_page_key 被当作匹配依据
```

## 正确修复

FineJob 必须把：

```text
有序 eligible Action 队列
```

同步给插件。

插件必须先拿当前真实页面匹配这个队列。

### greeting 匹配

继续复用原有：

```text
URL/pathname
read-only probe
encryptJobId
currentPageTaskId（如果现有链仍需要）
```

插件只有在当前岗位页与某个 greeting Action 匹配后，才携带该明确 Action ID 请求 lock/claim。

### chat_message / resume_send 匹配

聊天页只要求现有：

```text
/web/geek/chat
```

这一真实聊天 leader 页面条件。

插件不得要求聊天 UI 当前切换到某个具体 HR。

插件在聊天页上，从当前有序队列中选择页面可执行的 `chat_message` / `resume_send` Action。

业务发送目标继续来自 Action 自身已有：

```text
bossId / peer identity
securityId
job/session payload
```

而不是当前 UI 中打开的具体 HR。

## 后端 claim

后端 claim API 必须：

```text
接收插件已经匹配出的明确 Action ID
→ 在同一个写事务中确认该 Action 仍 eligible
→ conditional claim
```

后端不得再次从所有 Action 中随便挑另一条。

后端必须继续防止并发 claim。

---

# 五、修复 2：接通现有前置 loading

审计已经确认：

```text
schedulePageLoadWait
```

当前存在，但 `page_opened` 成功后没有实际调用它。

当前错误链：

```text
page_opened
→ startPageChecks
→ requestCurrentPageProbe
```

修复成：

```text
page_opened
→ schedulePageLoadWait(task, startPageChecks)
→ loading 完成
→ startPageChecks
→ requestCurrentPageProbe
```

要求：

1. Codex 必须复用现有 loading 时长、状态和函数。
2. Codex 不得新造第二套 loading timer。
3. loading 完成之前，插件不得 probe。
4. loading 完成之前，插件不得 match。
5. loading 完成之前，插件不得 claim。

如果现有函数参数需要小范围适配，可以做最小适配。

---

# 六、修复 3：结果回报后统一进入原 cooldown

当前 greeting 已有：

```text
结果同步
→ scheduleTaskCooldown
```

当前 chat/resume 是：

```text
sender
→ report result
→ FineJob complete
→ processAccounts
→ 立即再次 claim
```

修复要求：

```text
所有三类真实外发动作
greeting
chat_message
resume_send
```

在 FineJob 成功接收本次执行结果之后，都必须进入同一个现有 cooldown gate。

正确顺序：

```text
sender 完成
→ 插件第一时间 report FineJob
→ FineJob 更新状态
→ 插件进入现有 cooldown
→ cooldown 完成
→ 才允许下一轮页面匹配
```

Codex 必须复用现有 cooldown 配置/状态。

Codex 不得复制第二套独立 cooldown 体系。

cooldown 未完成时：

```text
chat heartbeat
新队列同步
新的 result flush
```

都不能触发新的真实 Action 执行。

---

# 七、修复 4：页面保留后先匹配当前页面

当前错误：

```text
结果同步
→ cooldown
→ requestTaskPage
```

修复后：

```text
结果同步
→ cooldown
→ 判断当前 BOSS 页面是否仍存在
```

如果当前页面仍存在：

```text
→ 使用最新同步队列启动当前页 probe/match
```

如果当前页面匹配到可执行 Action：

```text
→ 按 FineJob 排序选择最靠前的匹配 Action
→ claim
→ Preflight
→ execute
```

只有当前页面没有任何可执行匹配 Action 时：

```text
→ requestTaskPage
→ 请求 FineJob 打开下一目标页
```

页面真实关闭仍由 FineJob 执行。

---

# 八、修复 5：保留原页面关闭配置

Codex 不得重新设计页面关闭策略。

Codex 必须保留当前已有：

```text
close_page_after_completion
```

及实际等价逻辑。

要求：

```text
是否关闭
→ 继续由现有配置决定

真正关闭页面
→ 继续由 FineJob 执行

插件
→ 只报告结果/发出已有关闭请求
```

Codex 不得把新统一调度逻辑改成：

```text
永远关闭
```

或：

```text
永远保留
```

---

# 九、修复 6：greeting 接回真实统一 Preflight

## 当前错误

旧 greeting 页面链仍然实际执行：

```text
页面匹配
→ legacy match_task
→ sender
```

但统一 `_greeting_preflight` 没有进入这条真实执行链。

同时统一 greeting 又被错误要求先绑定既有聊天 session/account。

## 修复要求

greeting 必须使用：

```text
FineJob 打开岗位页
→ loading
→ probe
→ encryptJobId 匹配
→ 明确 Action ID claim
→ greeting Preflight
→ 原 sender 最终安全检查
→ 真正执行
```

greeting Preflight 至少检查：

```text
当前仍是目标岗位
encryptJobId 仍匹配
岗位仍允许执行
当前仍未沟通过
没有同岗位已经 succeeded 的重复 greeting
没有同岗位结果 unknown 的 greeting
```

原 sender 自己的：

```text
登录状态
页面 identity
encryptJobId
securityId
contacted == false
```

必须继续保留。

## 移除错误前置条件

Codex 必须移除：

```text
greeting 必须先存在唯一聊天 session/account 才能进入调度
```

Codex 不得删除 chat sender 自己需要的账号身份安全检查。

---

# 十、修复 7：chat_message 使用真实最新状态做 Preflight

## 当前错误

当前调用在上传 raw delta 后，Preflight 仍把：

```text
action.base_conversation_revision
```

当成最新：

```text
conversation_revision
```

传给后端。

调用方也没有提供：

```text
message_decisions
replacement_text
independent_text
```

因此后端无法完成真实分类分支。

## 修复要求

真正执行前：

```text
读取目标 HR 最新聊天 delta
→ raw ingest
→ semantic classify
→ 更新 read/reply state
→ 得到数据库真实最新 conversation revision
→ Preflight 使用真实最新 revision
→ 后端基于已保存 raw + semantic + Action 基线做 decision
```

不要把 Action 创建时的基线版本冒充当前版本。

---

# 十一、chat_message 五种分支必须实现

假设原消息是 A，原计划回复是 Reply-A。

## 1. 没有新消息

```text
Reply-A
→ Preflight passed
→ dispatch
```

## 2. 新消息 B 与 A 属于同一上下文

```text
Reply-A 不发送
→ 原 Action superseded
→ 根据 A+B 生成 replacement
```

replacement 继承原业务顺序位置。

如果 Reply-A 曾由用户人工确认，新文本变化：

```text
replacement → awaiting_confirmation
```

旧确认不能授权新文本。

## 3. B 是独立的新问题

用户要求：

```text
Reply-A 保留
→ Reply-A 暂时不单独发送
→ 为 B 生成自己的后续动作/回复
→ 等待 B 的后续动作准备完成
→ 如果 B 需要人工确认，等待 B 确认完成
→ A 和 B 都 ready
→ 再释放 A
→ A 先发送
→ B 后发送
```

这里的“一起发送”含义是：

```text
A 和 B 都准备好之后
→ 按业务顺序连续进入执行
```

不是把两段文本合并成一条消息。

### 必须增加等待屏障

当前实现“创建 B 后返回 passed 让 A 立即发送”是错误的。

修复后：

```text
创建/确认 B 后
→ A 返回 waiting/replan
```

A 必须等待 B 达到“后续动作已经准备完成”的状态。

B ready 后：

```text
A 可以重新 Preflight 并通过
→ session 顺序保证 A 先于 B
```

Codex 应优先复用已有：

```text
waiting_reason
status
session_sequence
revision
```

等机制。

Codex 不要新建复杂工作流引擎。

## 4. B 是平台事件/广告/无需回复

如果语义层已经得到：

```text
reply_required=false
```

则：

```text
更新 semantic/read/reply/job activity
→ Reply-A 继续
```

不得创建普通 AI reply Action。

## 5. 无法可靠判断

```text
classification_uncertain
→ 禁止 dispatch
→ waiting / needs_review / replan_required
```

系统不得猜测后自动发送。

---

# 十二、修复 8：resume_send 使用执行时新鲜附件列表

当前已经正确的部分必须保留：

```text
要求 recruiter_text 真人回复
平台事件不算真人回复
阻止 succeeded/unknown 重复发送
encryptResumeId + filename 精确校验
不得偷偷换简历
```

当前错误是：

```text
Preflight 只检查数据库里的旧 resume_list 缓存
```

修复要求：

```text
真正 resume_send dispatch 前
→ 使用现有只读 resume-list 能力获取最新附件列表
→ 精确查找 Action 已保存的 encryptResumeId + filename
```

如果仍存在：

```text
继续
```

如果不存在：

```text
停止发送
→ stale/blocked/waiting，按现有最合适状态处理
```

Codex 不得：

```text
自动选择另一份简历
```

继续保持：

```text
0份 → 阻止
1份 → 创建/选择阶段可自动唯一选择
多份 → 用户明确选择
```

执行阶段只验证，不重新选择。

---

# 十三、修复 9：resume_send 结果必须有可信 reconciliation

当前错误：

```text
resume_send
→ transport accepted
→ complete_action accepted
→ 没有 resume_send outbound reconciliation
```

导致后续同 HR Action 永久被 `accepted` 前驱卡住。

修复要求：

1. `transport accepted` 继续不能等于 `succeeded`。
2. Codex 必须把 `resume_send` 接入已有可信 outbound observation / reconciliation 体系。
3. 只有与当前 resume Action 精确关联的可信平台证据才能提升为 `succeeded`。
4. 如果没有可信确认，Action 必须进入/保持保守状态，最终应能成为 `unknown`，不得自动业务重试。
5. 同 HR 后续 Action 在 predecessor `unknown` 时继续等待。

可使用已经存在的 semantic/action correlation 能力。

Codex 不得仅凭本机发送意图、MQTT publish callback 或本地 WebSocket send hook 标记成功。

---

# 十四、修复 10：greeting 的 accepted 不能直接变 succeeded

审计确认这是 HEAD 已存在的问题，但它直接违反当前统一结果语义，因此本次一起修复。

要求：

```text
sender 返回 accepted
≠
业务 succeeded
```

只有已有可信平台状态观察可以提升到 `succeeded`。

如果当前 greeting 已有发送后可信页面状态，例如能够可靠重新观察：

```text
contacted == true
```

Codex 可以复用该真实观察作为成功证据，但必须保持现有页面 identity 关联。

如果当前代码没有可信成功证据：

```text
accepted / unknown
```

必须保持保守，不能直接写 `succeeded`。

Codex 不得为解决这个问题重新研究或修改 MQTT/Techwolf 协议。

---

# 十五、同 HR 顺序必须同时覆盖 chat_message 和 resume_send

现有 `session_sequence` 已经同时覆盖：

```text
chat_message
resume_send
```

这部分保留。

修复后必须保证：

```text
A：chat_message
B：resume_send
C：chat_message
```

如果业务产生顺序是：

```text
A → B → C
```

实际执行也是：

```text
A → B → C
```

以下状态不能被后续动作越过：

```text
claimed
preflighting
dispatching
accepted（尚未确认）
unknown
waiting for independent follow-up ready
```

只有前驱进入允许后续继续的明确状态后，后继才 eligible。

---

# 十六、不要引入这些错误设计

本次修复严禁新增或强化：

```text
account scheduler
account affinity
target_page_kind 作为实际 matcher
target_context_key 作为聊天 UI matcher
具体 HR session UI 切换
page kind score
插件直接打开页面
插件直接关闭页面
Scheduler 决定页面关不关
新的 loading 状态机
新的 cooldown 状态机
```

`target_page_kind/target_page_key/target_context_key` 如果当前 schema 已存在，可以暂时保留兼容字段。

但是它们不能成为本次真实执行权威。

---

# 十七、必须增加/修改的离线测试

本轮禁止真实 BOSS side effects。

至少覆盖以下回归测试。

## 页面打开与 loading

```text
page_opened 后
loading 未完成
→ probe 不发生
→ match 不发生
→ claim 不发生

loading 完成
→ 才允许 probe/match
```

## 页面匹配与 claim

```text
当前岗位页 encryptJobId 匹配 greeting A
→ 只能 claim A

当前聊天页
→ 不能 claim greeting

后端收到明确 Action ID claim
→ 只能 claim该 ID
→ 不得换成另一条 Action
```

## cooldown

```text
Action 结果已经 report FineJob
→ cooldown 开始

cooldown 未结束
→ chat heartbeat 不得触发下一次执行
→ 新队列同步不得触发下一次执行

cooldown 结束
→ 才允许下一轮 match
```

## 页面保留

```text
当前页面仍存在且能匹配最新队列
→ cooldown 后先执行当前页匹配任务
→ 不调用 requestTaskPage

当前页面无匹配
→ 才 requestTaskPage
```

## chat Preflight

覆盖：

```text
无新消息
相关补充
独立新问题
平台事件无需回复
classification_uncertain
```

独立新问题必须验证：

```text
创建 B
→ A 不 dispatch
→ B 未 ready 时 A waiting
→ B ready 后 A 先执行
→ B 后执行
```

## resume_send

覆盖：

```text
真人 HR 回复才允许
平台事件不算真人回复
原 resume ID + filename 仍存在
原 resume 不存在时阻止
不得自动换简历
已有 succeeded 阻止重复
已有 unknown 阻止重复
transport accepted 不直接 succeeded
可信 outbound observation 才 succeeded
```

## greeting

覆盖：

```text
无需已有聊天 session/account 才能进入执行
岗位页面匹配后才 claim
Preflight 在 claim 后、sender 前
contacted=true 阻止
同岗位 unknown 阻止重复
accepted 不直接 succeeded
```

---

# 十八、Codex 完成后必须输出

Codex 完成代码修改和离线测试后，第一行输出：

```text
POST_CUTOVER_PATCH_IMPLEMENTED
```

或者：

```text
POST_CUTOVER_PATCH_BLOCKED
```

然后必须继续输出完整说明。

每一项修改必须写：

```text
修改目的：
修改文件：
修改函数：
修改前行为：
修改后行为：
为什么这是最小修复：
对应测试：
```

Codex 还必须单独输出：

```text
保留未改的原执行器行为
保留未改的 Stage 1 能力
仍然无法离线确认的事项
实际运行前风险
```

如果某一项无法安全实现，Codex 必须停止该项并说明原因。

Codex 不得为了让测试通过而扩大架构改造。

---

# 十九、完成条件

只有以下全部满足，本任务才算完成：

```text
FineJob 打开页面后先 loading
loading 完才 probe/match
页面匹配后才 claim
claim 后才 Preflight
Preflight 后才 dispatch
执行结果先立即回报 FineJob
回报后进入原 cooldown
cooldown 后先匹配当前保留页面
当前页无匹配才请求 FineJob 开新页
greeting 不再依赖既有聊天账号绑定
chat 独立新问题会等待后续动作准备好再按顺序发送
resume_send 使用执行时新鲜附件验证
chat/resume/greeting 的 accepted 不被错误当作业务成功
同 HR chat_message + resume_send 顺序继续成立
没有新增具体 HR UI session 切换
没有新增账号调度体系
没有真实 BOSS side effects
```

本任务完成后，不要立即提交。

下一步需要独立代码审查确认：

```text
POST_CUTOVER_PATCH_PASS
```

后再决定提交。
