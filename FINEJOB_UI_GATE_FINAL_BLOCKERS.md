# FineJob：UI 前最后一轮真实阻断修复

## 目标

只修 Sol 最终验收确认的 3 个真实业务阻断。

本轮修完后，不再扩范围；只做定向验证，通过后直接进入 UI 收尾。

当前已确认正常的部分全部保留：

```text
loading
页面匹配后 exact-ID claim
chat relation 五分支
greeting unified bridge
greeting exception identity
canonical succeeded 单调性
resume_send Preflight snapshot
resume_list 创建阶段
pause/resume 后端 Action 收口
shared gate 正常路径
cooldown 正常路径
当前页复匹配
FineJob 页面开关责任
```

禁止：

```text
重写 Executor
重写 Scheduler
重写页面生命周期
重写 chat relation
重写 greeting bridge
重写 resume snapshot
修改 MQTT/Techwolf
跑 backend 全量 pytest
跑与本任务无关的测试
修改无关模块
```

---

# 1. 修复 chat 预 dispatch 异常永久占用 shared gate

## 当前问题

当前：

```text
tryEnterBusy()
→ claim / resume snapshot / Preflight / dispatch_started
→ 其中任一步抛异常
→ 外层 catch 只记 lastError
→ shared gate 仍 busy
→ greeting/chat/resume 全部永久停住
```

## 正确行为

必须按阶段收口：

```text
进入 gate
→ claim
→ snapshot（resume）
→ Preflight
→ dispatch_started
```

在真正完成 dispatch handoff 之前：

```text
任何异常
→ 后端状态按当前阶段安全收口
→ shared gate 释放
```

只有已经进入真实 dispatch 边界、并且存在可恢复 active action identity 时，gate 才允许继续保持 busy。

## 要求

修改：

```text
chat-coordinator.ts
claimAndDispatch
processAccounts
```

使用清晰的阶段标志，例如：

```text
gateAcquired
claimed
dispatchStarted
```

可以用 `try/finally`，但不能无脑 finally release：

```text
dispatchStarted == true
```

以后必须由 result sync 路径负责释放/进入 cooldown。

需要覆盖：

```text
claim 抛错
resume snapshot 抛错
Preflight 抛错
dispatch_started 请求抛错
```

---

# 2. 修复 pause / disconnect / greeting handoff 的 gate 生命周期

## 2.1 cooldown 中 pause

当前：

```text
result sync
→ cooldown
→ pause
→ stopExecutionWaits 清 timer
→ gate 仍 cooldown
→ resume 后永远 busy
```

必须变成：

```text
pause 清 cooldown timer
→ 同时结束对应 shared gate cooldown
```

恢复后 gate 必须回到可继续调度的状态。

## 2.2 disconnect

当前：

```text
clearLocalConnection
→ 清本地状态
→ gate 没按执行阶段收口
```

必须区分：

### 尚未 dispatch

```text
释放 gate
```

### 已 dispatch / side effect 结果不确定

```text
不能直接释放成 idle 并开始下一动作
→ 保留 execution identity
→ 按现有保守语义回写 unknown / 等待补偿结果
→ result sync 后进入 cooldown
```

不能制造重复发送。

## 2.3 greeting dispatch_started 后 WS handoff 失败

当前：

```text
greeting unified dispatch_started
→ startDefaultGreetingAfterMatch
→ sendControlMessage == false
→ 直接 return
→ unified Action 卡 dispatching
→ gate 卡 busy
```

必须变成：

```text
dispatch_started 已成功
→ handoff 失败
→ 使用当前 unifiedActionId + unifiedExecutionEpoch
→ 回写明确 failed/unknown（按现有 side-effect 边界选择保守语义）
→ FineJob canonical 收口
→ shared cooldown
```

不能：

```text
直接释放 gate
```

因为 backend 已经进入 dispatch_started。

也不能：

```text
永久卡 dispatching
```

---

# 3. 修复同 HR session_sequence gap

## 当前问题

`_session_ready` 只检查最近一个更早 Action：

```text
ORDER BY session_sequence DESC
LIMIT 1
```

例如：

```text
seq1 = unknown
seq2 = cancelled
seq3 = queued
```

当前只看到 seq2，所以 seq3 被错误放行。

## 正确行为

对于目标 Action：

```text
只要任一更早 session_sequence
仍处于阻塞状态
→ 当前 Action 不 ready
```

阻塞状态至少包括当前项目真实等价状态：

```text
waiting
awaiting_confirmation（如果属于统一 Action 状态）
claimed
preflighting
dispatching
accepted
unknown
```

终态例如：

```text
cancelled
superseded
stale
succeeded
failed（按当前业务是否允许后继继续）
```

不得遮蔽更早的阻塞 Action。

## 推荐最小实现

不要再：

```text
取最近前序 LIMIT 1
```

改为：

```text
查询是否 EXISTS 任一更早 blocking Action
```

或：

```text
读取所有更早 Action 并判断 blocking 集合
```

必须保持：

```text
unknown 永远阻塞后继
```

---

# 4. 本轮只做定向测试

禁止 backend 全量 pytest。

只跑以下定向测试，目的明确：

## shared gate 异常

验证：

```text
claim throw → gate release
snapshot throw → gate release
Preflight throw → gate release
dispatch_started throw → gate release/正确收口
```

## pause / disconnect / greeting handoff

验证：

```text
cooldown 中 pause → gate 不残留 cooldown
未 dispatch disconnect → gate release
已 dispatch disconnect → 不错误放行下一动作
greeting dispatch_started 后 handoff fail → Action 收口 + cooldown
```

## session_sequence gap

验证：

```text
seq1 unknown
seq2 cancelled
seq3 queued
→ seq3 不 ready
```

以及至少：

```text
seq1 accepted
seq2 succeeded
seq3 queued
→ seq3 不 ready

seq1 succeeded
seq2 cancelled
seq3 queued
→ seq3 ready
```

---

# 5. 本轮不得做的事情

不要：

```text
跑 backend 全量
跑 desktop 全量
跑 extension 全量
重跑 ACL/tmp_path 错误
处理 PDF / 检索 / Web Draft / 公司治理 / Codex JSONL
修改旧 API 测试契约
修改 resume_list 业务语义
重新允许无 action_id claim
```

---

# 6. 完成输出

第一行：

```text
UI_GATE_FINAL_BLOCKERS_FIXED
```

或：

```text
UI_GATE_FINAL_BLOCKERS_BLOCKED
```

然后只报告：

```text
1. pre-dispatch gate exception
2. pause/disconnect/handoff gate lifecycle
3. session_sequence gap
4. 修改文件/函数
5. 定向测试结果
6. 仍存在的真实 blocker
```

---

# 7. UI 入口条件

只有以下三项都修完，才允许：

```text
UI_GATE_FINAL_BLOCKERS_FIXED
```

```text
pre-dispatch 异常不会永久占 gate
pause/disconnect/handoff 不会留下永久 busy/cooldown/dispatching
session_sequence 不会越过更早 unknown/accepted 等阻塞 Action
```

---

## 本轮完成记录

已完成 3 个真实 blocker 的最小修复：

```text
chat claim / snapshot / Preflight / dispatch_started 前异常不再永久占用 shared gate
pause 清 cooldown timer 时同步结束 shared gate cooldown；disconnect 按 dispatch 阶段收口
greeting dispatch_started 后 WS handoff 失败以 unified identity 回写 unknown 并进入 cooldown
_session_ready 检查任一更早 blocking Action，不再被中间 terminal Action 遮蔽
普通 WebSocket 非 4001 close 按 pending / dispatch 阶段收口并保留重连，既有 cooldown timer 保留至自然结束
```

定向验证已通过：

```text
boss-executor-extension/tests/boss-chat-coordinator.test.ts
boss-executor-extension/tests/finejob-client.test.ts
backend/tests/fine_job/test_action_scheduler.py
```

普通 WebSocket 非 4001 close 的 cooldown 保留路径已补充定向测试。

```text
boss-executor-extension/tests/finejob-client.test.ts：18 passed
```

完成后停止 Executor/Scheduler 扩范围开发，直接进入 UI 收尾。
