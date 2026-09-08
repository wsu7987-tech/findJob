# FineJob：Final Fix — 剩余问题一次完成（resume_list + pause/resume）

## 目标

本任务一次性修完 Sol 最终审查剩余的两个问题：

```text
问题 2：创建阶段 resume_list 不可达。
问题 4：暂停会话时没有收口 fj_actions 中尚未发送的 unified chat/resume Action。
```

本轮只改代码。

**不要运行测试。不要修改测试文件。不要为了旧测试通过而改变业务逻辑。**

当前所有已经完成的 cutover、chat relation、greeting bridge、resume Preflight snapshot、shared gate、cooldown、当前页复匹配、canonical 状态单调性全部保留。

不得 reset / restore / checkout 覆盖工作区 / clean / stash / commit。

---

# A. 恢复创建阶段 resume_list 只读可达链

## 当前问题

当前链路断在：

```text
桌面请求刷新附件
→ create_resume_list_action
→ resume_list 写入 fj_chat_send_actions
→ unified eligible 不返回这个 helper
→ chat coordinator 无法发现它
→ listResumeAttachments 不会执行
→ 首次读取附件与 0/1/多份选择不可用
```

## 正确行为

`resume_list` 继续只是只读 helper，不是 unified business Action。

必须形成：

```text
创建 resume_list helper
→ 插件能够发现 pending helper
→ 获得明确 helper action_id
→ exact-ID claim
→ 复用现有 ResumeSnapshotCommand / ResumeSnapshotResult
→ Main World 调用现有 BossChatSender.listResumeAttachments
→ 回传 encryptResumeId + filename metadata
→ FineJob 完成只读 helper
→ 原 0/1/多份简历选择流程继续
```

### 必须遵守

```text
0 份 → 阻止创建 resume_send
1 份 → 唯一自动选择
多份 → 必须用户明确选择
```

浏览/选择附件简历不要求 HR 已回复。

真正执行 `resume_send` 时才要求真实 recruiter reply。

### 不能做

不得：

```text
把 resume_list 变成 fj_actions 的真实业务 Action
让 resume_list 产生 accepted / unknown / succeeded
让 resume_list 进入 session_sequence 的真实发送排序
让 resume_list 触发发送后的 cooldown
重新允许“不给 action_id 后端自行换领”
新增第二套附件读取 API
修改已经完成的 resume_send Preflight snapshot 链
```

优先复用当前已经存在的：

```text
ResumeSnapshotCommand
ResumeSnapshotResult
BossChatSender.listResumeAttachments
```

如果现有 resume_list claim 已支持 exact-ID，只补“pending helper discover”入口即可。

---

# B. pause/resume unified Action 收口

## 当前问题

当前：

```text
用户确认回复
→ 创建 fj_actions 中 queued chat_message / resume_send
→ 用户暂停 session
→ 只取消旧 fj_chat_send_actions
→ unified Action 仍存在
→ 后续恢复 session
→ 暂停前旧 Action 重新 eligible
→ 有错发风险
```

## 正确行为

当用户将一个 HR session 设为 paused / manual takeover 时：

```text
旧表尚未发送的动作
+
fj_actions 中该 session 尚未真实 dispatch 的 chat_message / resume_send
```

必须一起收口。

### 允许取消/终止的 unified 状态

只处理**确认没有发生真实 side effect**的动作，例如当前状态机中的等价状态：

```text
awaiting_confirmation
queued
waiting
```

对于：

```text
claimed
preflighting
```

必须根据当前 shared gate / lease 语义安全处理：

- 如果明确尚未进入 dispatch_started，可取消并释放执行资格；
- 必须避免与正在执行的 claim/preflight 产生竞态；
- 需要原子条件更新，不能无条件覆盖。

### 不能粗暴取消的状态

不得把以下状态直接当作“没发送”取消：

```text
dispatching
accepted
unknown
succeeded
```

因为这些状态可能已经产生真实外发。

其中：

```text
unknown
```

继续保持保守，不自动重试。

### pause 后必须保证

```text
暂停前尚未真实发送的旧 Action
→ 恢复 session 后不会重新变成 eligible
```

如果项目当前已有：

```text
cancelled
superseded
stale
```

等合适终态，复用现有语义。

不要新增新的状态枚举，除非当前状态机确实无法表达。

### coverage / reply task

暂停时还必须检查与被取消 Action 对应的：

```text
reply task
message coverage / reply_state
```

避免出现：

```text
Action 已取消
但消息仍被标记 queued/replied
```

应恢复为当前项目已有的“等待人工/未自动覆盖”语义。

不要删除 raw message。

---

# C. resume 后的行为

恢复 session 时：

```text
只允许恢复之后新产生、重新规划或用户重新确认的 Action 进入执行
```

不得自动复活暂停前已经取消/失效的 unified Action。

如果用户希望继续旧问题，应走当前已有：

```text
重新生成 / 重新确认 / 新 revision Action
```

而不是重新启用原 Action。

---

# D. 并发与状态边界

本轮必须保持当前已经完成的 shared gate。

pause 操作如果遇到正在执行的 Action：

```text
不要绕过 gate
不要强制把 dispatching 改 cancelled
不要制造第二次发送
```

对尚未 dispatch 的 Action，应使用条件状态更新，避免 pause 与 claim 同时发生时出现：

```text
pause 以为取消成功
但 executor 已经拿到真实 dispatch 授权
```

---

# E. 本轮禁止修改

不要修改：

```text
chat relation 五分支
independent B planning 规则
greeting legacy→unified bridge
greeting sender
canonical succeeded 单调性
shared action gate
cooldown
currentPageTaskId
当前页复匹配
resume_send Preflight snapshot
MQTT/Techwolf
FineJob 页面打开/关闭
URL/encryptJobId matcher
```

---

# F. 本轮不要测试

用户明确要求：

```text
Terra 只施工。
Sol 负责测试和独立验收。
```

所以本轮：

```text
不要运行 pytest
不要运行 vitest
不要运行 typecheck
不要修改测试文件
```

允许只读现有测试来理解接口。

---

# G. 完成后输出

第一行：

```text
FINAL_REMAINING_FIXES_IMPLEMENTED
```

或：

```text
FINAL_REMAINING_FIXES_BLOCKED
```

然后说明：

```text
1. resume_list discover 入口
2. exact-ID claim
3. ResumeSnapshot/listResumeAttachments 复用
4. 0/1/多份选择链
5. pause 时 unified Action 收口
6. claimed/preflighting 并发处理
7. dispatching/accepted/unknown 的保护
8. resume 后为什么旧 Action 不会复活
9. 修改文件和函数
10. 仍存在的风险
```

不要输出测试结果，因为本轮禁止运行测试。

---

# H. IMPLEMENTED 标准

只有以下全部完成才能输出：

```text
FINAL_REMAINING_FIXES_IMPLEMENTED
```

```text
resume_list helper 可被插件发现
resume_list exact-ID claim 可达
复用现有只读附件 snapshot 能力
0/1/多份选择链恢复
resume_list 不进入业务 Action completion/cooldown

pause 同时收口旧表和 fj_actions 中尚未 dispatch 的 chat/resume
pause 与 claim/preflight 有安全并发条件
dispatching/accepted/unknown/succeeded 不被粗暴取消
恢复 session 不会复活暂停前旧 unified Action
raw message 保留且 coverage/reply state 不产生错误已回复语义

所有已完成的 chat/greeting/resume/shared-gate/page lifecycle 修复不被重写
