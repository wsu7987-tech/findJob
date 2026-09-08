# FineJob：Post-Cutover 最终独立审查任务（Sol）

## 任务主要为了完成什么

本任务只做最终独立审查。

Sol 必须确认当前**未提交工作区**已经把 `FINEJOB_SCHEDULER_PREFLIGHT_CUTOVER` 的已知问题修正，并且没有破坏原本能工作的执行链。

本任务解决的问题是：

> 当前 chat relation、greeting unified bridge、resume Preflight snapshot 都已经分别完成。现在需要一个没有参与实现的模型，从当前真实代码出发，确认三部分组合起来以后仍然是一条正确、唯一、不会重复发送的执行链。

本任务不修改代码。
本任务不提交代码。
本任务不访问真实 BOSS。

---

# 一、审查基准

当前 Git 状态：

```text
HEAD
= cutover 前的已提交基线

当前工作区
= cutover + 后续全部修复
= 尚未提交
```

Sol 必须检查：

```text
git status
git diff HEAD
git diff --cached HEAD
未跟踪源文件和测试文件
```

Sol 不得执行：

```text
git reset
git restore
git checkout 覆盖工作区
git clean
git stash
git commit
```

---

# 二、最终要求的总执行链

Sol 必须确认当前真实调用链满足：

```text
FineJob 维护并同步有序 eligible Action 队列
→ 插件收到队列

如果 FineJob 刚打开页面：
→ 完成原前置 loading
→ loading 完成后才 probe

插件使用当前真实页面匹配执行队列
→ 页面匹配成功
→ 对明确 Action ID 做 conditional claim
→ Preflight
→ dispatch_started
→ 原 sender
→ 真实 side effect
→ 插件第一时间报告 FineJob
→ FineJob 更新统一 Action / 兼容状态
→ 原 cooldown

cooldown 完成：
→ 如果当前页面仍存在，先重新 probe/match 当前页面
→ 当前页存在可执行匹配：继续
→ 当前页没有匹配：插件才请求 FineJob 打开下一目标页
```

禁止存在：

```text
页面匹配前 claim
page_opened 后立即 probe、跳过 loading
执行完成后立即再次 claim、跳过 cooldown
当前页面可复用时直接打开新页面
插件自己直接打开/关闭页面
```

---

# 三、页面责任边界

Sol 必须确认：

```text
FineJob
→ 实际打开页面
→ 实际关闭页面

插件
→ 请求 FineJob 打开/关闭
→ 不直接控制浏览器页面生命周期
```

原 `close_page_after_completion` 或当前等价配置必须继续真实生效。

Sol 必须确认新统一调度没有把页面策略固定成“永远关闭”或“永远保留”。

---

# 四、页面匹配不能被新体系替代

Sol 必须确认真实执行仍复用：

```text
URL / pathname
read-only probe
encryptJobId
现有任务 identity
```

以下字段即使仍存在 schema，也不能成为新的执行权威：

```text
target_page_kind
target_page_key
target_context_key
page affinity
account affinity
account scheduler
```

Sol 必须特别检查 `target_page_key`、`account_uid` 是否仍在真实 claim eligibility 中错误改变 greeting 行为。

Chat sender/auth 的局部身份安全字段可以保留。

---

# 五、chat_message 最终审查

Sol 必须从真实代码确认以下调用链已经接通：

```text
最新 raw delta
→ derive semantic
→ message relation service
→ reply planning
→ backend Preflight
→ dispatch
```

## 五种情况

### 1. 无新消息

```text
A 原回复继续
```

### 2. B 与 A 相关

```text
A 不发送
→ A superseded
→ 现有 reply generation 生成 A'
→ A' 覆盖 A+B
→ A' 继承 A 的 session_sequence
```

如果 A 曾人工确认且 A' 文本变化：

```text
A' 必须重新 awaiting_confirmation
```

### 3. B 是独立问题

必须是：

```text
A 保留
→ A waiting
→ B 进入现有 reply planning
→ B 生成自己的 unified Action
→ B 未 ready / 未确认时 A 不发送
→ B ready 后 A barrier 解除
→ A 先发送
→ B 后发送
```

“一起发送”不是文本合并，而是两个动作都 ready 后按顺序执行。

### 4. B 是无需回复平台事件

```text
reply_required=false
→ 不调用 relation AI
→ 不创建普通 reply
→ A 继续
```

### 5. 无法判断

```text
classification_uncertain
→ A waiting
→ 不 dispatch
```

---

# 六、message relation service 审查

Sol 必须确认：

```text
relation service 独立于 action_scheduler
复用现有 AI provider/client
不新增第二套 provider
只输出受限结构化枚举
provider error/非法输出 → classification_uncertain
```

必须确认 relation audit 至少可追溯：

```text
action_id
session_id
base message ids
new message ids
conversation revision
decision
reason_code
classifier/provider version
```

不得使用简单关键词/问号/字符串 contains 作为“相关/独立”的执行权威。

---

# 七、同一 HR 的真实外发顺序

Sol 必须确认同一个 HR 会话中的：

```text
chat_message
resume_send
```

共同受顺序约束。

例如：

```text
A chat_message
B resume_send
C chat_message
```

如果业务顺序为 `A → B → C`，实际执行不能越序。

以下前驱状态不能被后继越过：

```text
claimed
preflighting
dispatching
accepted（尚未确认）
unknown
独立 B 尚未 ready 时 A 的 waiting barrier
```

尤其确认 `unknown` 表示真实外发结果无法确认，不是 AI 文本理解不确定。

`unknown` 不得自动业务重试。

---

# 八、greeting bridge 最终审查

Sol 必须确认原页面链没有被重写：

```text
legacy greeting
→ requestTaskPage
→ FineJob open_task_page
→ loading
→ probe
→ URL/encryptJobId match
```

只有页面匹配成功后才：

```text
legacy task id
→ source_table/source_id 找唯一 unified greeting
→ 指定 unified action_id claim
→ unified greeting Preflight
→ unified dispatch_started
→ 原 sender
```

必须确认：

```text
mapping missing → fail closed
mapping ambiguous → fail closed
unified claim fail → sender 不执行
Preflight fail → sender 不执行
unified terminal/unknown → legacy 不重复发送
```

---

# 九、greeting bridge identity 与结果权威

Sol 必须确认结果中存在足够的 unified execution identity，例如：

```text
unifiedActionId
unifiedExecutionEpoch
```

以及当前接口真实要求的 token/owner。

sender 结果必须：

```text
优先更新 unified canonical outcome
→ 再同步 legacy compatibility status
```

legacy shadow sync 不能反向覆盖 unified canonical 状态。

必须确认 `accepted != succeeded` 仍然成立。

---

# 十、greeting sender 原安全检查必须保留

Sol 必须确认以下原检查没有被 bridge 删除：

```text
登录状态
页面 identity
encryptJobId
securityId
contacted == false
```

Preflight 是额外保护，不是替代 sender。

---

# 十一、resume_send 最终 Preflight 顺序

Sol 必须确认真实顺序已经变成：

```text
claim
→ Background 发 resume snapshot request
→ Main World 调用现有 listResumeAttachments
→ 独立 snapshot result
→ Background 提交 backend Preflight
→ backend 精确验证 Action 原 encryptResumeId + filename
→ Preflight passed
→ dispatch_started
→ 原 sender
→ sender 内最后一次附件防御校验
→ side effect
```

不得仍存在：

```text
dispatch_started
→ 才第一次刷新附件
```

---

# 十二、resume snapshot 通道

Sol 必须确认 snapshot：

```text
有独立 request/result 类型
带 request_id + action_id
只返回必要 attachment metadata
不返回 PDF 正文
不复用 Action completion result
不触发 accepted/failed/unknown/succeeded
```

以下情况必须 fail closed：

```text
timeout
Main World error
request_id mismatch
action_id mismatch
格式非法
原 resume ID 不存在
filename 不匹配
```

不得自动换另一份简历。

---

# 十三、resume 业务规则

Sol 必须确认继续保持：

```text
0 份简历 → 阻止
1 份简历 → 创建/选择阶段可自动唯一选择
多份简历 → 用户明确选择
```

执行阶段：

```text
只验证已经固定的 encryptResumeId + filename
不重新选择
```

必须确认只有真实 recruiter reply 才满足 resume_send 前置条件。

平台广告、附件状态、简历已读等事件不能被当作 HR 真人回复。

---

# 十四、统一结果语义

三种真实动作：

```text
greeting
chat_message
resume_send
```

都必须保持：

```text
transport accepted
≠
business succeeded
```

Sol 必须确认：

```text
本机发送意图
MQTT publish callback
本机 WebSocket send
```

不能单独把 Action 提升为 succeeded。

可信 outbound observation / reconciliation 才能完成业务成功。

无法确认时保持 `accepted / unknown` 的保守语义。

---

# 十五、旧执行入口不得造成重复发送

Sol 必须重点检查：

```text
fj_automation_actions
旧 chat/resume claim API
旧 shadow sync
新 unified claim
```

是否仍存在两条可以对同一个 source Action 产生真实 side effect 的路径。

必须证明：

```text
同一个业务 Action
最多只有一个真实 dispatch 权威路径
```

尤其检查 legacy greeting sender + unified greeting bridge 组合后是否可能重复 completion / duplicate dispatch。

---

# 十六、loading / cooldown 最终审查

Sol 必须确认：

```text
page_opened
→ 现有 loading
→ loading 完成才 probe
```

以及：

```text
sender result
→ 第一时间 report FineJob
→ FineJob 更新状态
→ cooldown
→ 下一轮 match
```

必须确认 chat/resume 不再绕过统一 cooldown gate。

必须确认 heartbeat / queue sync 在 cooldown 中不会启动真实外发。

---

# 十七、页面保留后的行为

Sol 必须确认：

```text
cooldown 完成
→ 当前页面仍存在
→ 先 probe/match 最新队列
```

只有当前页面没有任何可执行匹配时，才：

```text
requestTaskPage
→ FineJob 打开新页面
```

---

# 十八、平台信息配置化不属于本轮

本轮不要求把平台语义规则做成配置化。

Sol 只检查当前语义分类是否继续正确参与：

```text
reply_required
Preflight
```

不得因为“尚未配置化”判本轮失败。

---

# 十九、必须检查的测试

Sol 必须审查并按需要运行离线测试。

至少覆盖：

```text
Stage 1 action store
action scheduler
message relation
chat relation E2E
greeting unified bridge
FineJob client
default greeting sender
resume snapshot
chat coordinator
chat sender
TypeScript typecheck
```

Sol 可以补充运行更多现有离线测试。

禁止真实 BOSS side effects。
禁止真实外部 AI provider 调用。

---

# 二十、Sol 输出格式

第一行必须是：

```text
POST_CUTOVER_FINAL_PASS
```

或者：

```text
POST_CUTOVER_FINAL_FIX_REQUIRED
```

或者：

```text
POST_CUTOVER_FINAL_UNCERTAIN
```

第一行之后必须继续完整输出审查结果。

逐项输出：

```text
1. 总执行顺序
2. FineJob / 插件页面责任
3. loading
4. 页面匹配 → claim
5. chat 五分支
6. A/B 顺序
7. greeting bridge
8. greeting result authority
9. resume snapshot → Preflight
10. accepted / succeeded / unknown
11. cooldown
12. 当前页复匹配
13. 重复 dispatch 风险
14. 测试结果
```

如果发现问题，每个问题必须写：

```text
问题：
当前真实行为：
正确行为：
文件：
函数：
调用链：
影响：
最小修复范围：
```

---

# 二十一、PASS 标准

只有以下全部满足才能输出：

```text
POST_CUTOVER_FINAL_PASS
```

必须同时满足：

```text
原页面生命周期保留
FineJob 实际开关页面
打开页面后先 loading
loading 后才 probe/match
页面匹配后才 claim
claim 后才 Preflight
Preflight 后才 dispatch
chat 五分支真实接通
独立 B 等待到 ready 后 A→B
同 HR chat/resume 不乱序
greeting bridge 真实接通 unified claim/Preflight/result
resume snapshot 在 dispatch_started 前进入 Preflight
sender 最终安全检查继续保留
accepted 不直接 succeeded
unknown 不自动 retry
执行结果先报告 FineJob 再 cooldown
cooldown 后先匹配当前保留页面
无重复业务 dispatch 路径
无 account scheduler / HR UI session / 新 page matcher 回归
所有相关离线测试通过
```

如果存在任何会导致真实错发、重复发送、绕过 loading/cooldown、错误 claim、错误成功状态的问题：

```text
POST_CUTOVER_FINAL_FIX_REQUIRED
```

如果仅凭静态代码和离线测试无法确认真实平台确认语义，不要因此直接判失败。Sol 应单独列入：

```text
仍需真实环境验证
```

但本轮禁止实际访问 BOSS。
