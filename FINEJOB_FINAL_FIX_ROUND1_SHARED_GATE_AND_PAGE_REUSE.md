# FineJob：Final Fix Round 1 — 共享执行门 + cooldown + 当前页复匹配

## 任务主要为了完成什么

本任务只修 Sol 最终审查中的两个执行链问题：

```text
问题 1：
greeting 与 chat/resume 仍然由两个并行循环启动真实外发，
两边看不到对方正在执行或正在 cooldown。

问题 7：
任务完成并 cooldown 后，
currentPageTaskId 仍绑定旧任务，
导致当前页面无法匹配最新队列里的下一任务。
```

本任务解决的结果必须是：

```text
任意一种真实外发进入 claim / Preflight / dispatch / 结果回写 / cooldown 后，
另外一条执行循环不能再启动新的真实外发。

cooldown 完成后，
插件先解除“旧已完成任务 ID”约束，
再使用当前页面 URL / probe / encryptJobId 匹配最新执行队列。
只有当前页面没有匹配任务时，插件才请求 FineJob 打开新页面。
```

本任务不处理其他 5 个 Sol 问题。
本任务不修改 chat relation。
本任务不修改 greeting bridge 的 claim/Preflight 业务逻辑。
本任务不修改 resume snapshot。
本任务不修改 MQTT/Techwolf。
本任务不访问真实 BOSS。

---

# 一、当前 Sol 已确认的正常行为必须保留

Sol 已确认以下正常链已经存在，本轮不得重写：

```text
Greeting：
FineJob 开页
→ loading
→ probe
→ encryptJobId match
→ legacy match
→ unified claim
→ unified Preflight
→ unified dispatch_started
→ 原 sender
→ result

Chat：
heartbeat
→ raw delta
→ semantic
→ relation
→ 指定 Action claim
→ Preflight
→ dispatch_started
→ sender
→ result

Resume：
claim
→ resume snapshot
→ backend Preflight
→ dispatch_started
→ sender 最后附件校验
→ side effect
```

本轮只修两个循环之间的共同执行门，以及 cooldown 后当前页复匹配。

---

# 二、禁止重新设计 Scheduler 或 Executor

Terra 不得：

```text
把 greeting 改成 chat coordinator 执行
把 chat/resume 改成 legacy greeting client 执行
合并两个类
重写 requestTaskPage
重写 open_task_page
重写 loading
重写 page probe
重写 greeting bridge
重写 chat relation
重写 resume snapshot
```

两个现有循环可以继续存在。

本轮只允许让两个循环共享同一个“现在能不能开始下一次真实外发”的状态。

---

# 三、共享执行门的职责必须非常小

共享执行门只回答：

```text
现在是否允许开始新的真实 Action？
```

它不负责：

```text
任务排序
页面匹配
选择 Action
claim SQL
Preflight 业务判断
页面开关
消息分类
```

建议状态只有：

```text
idle
busy
cooldown
```

具体实现名称按现有项目风格。

不要增加复杂状态机。

---

# 四、共享执行门必须由 Background 创建一次

当前 Background 同时启动：

```text
FineJobExecutorClient
BossChatCoordinator
```

Terra 必须在共同上层创建同一个 gate 实例/同一组状态回调，然后同时提供给：

```text
FineJobExecutorClient
BossChatCoordinator
```

禁止：

```text
client 自己一个 gate
coordinator 自己另一个 gate
```

否则问题仍然存在。

---

# 五、什么时候必须阻止新的 claim

任一真实 Action 已进入以下任一阶段：

```text
claimed
preflighting
dispatching
正在等待 sender result
正在把 result 回写 FineJob
cooldown
```

另一循环必须在 claim 前停止。

也就是说：

```text
greeting 已 claim / Preflight / dispatch / result-write / cooldown
→ chat/resume 不能 claim

chat/resume 已 claim / Preflight / dispatch / result-write / cooldown
→ greeting 不能进入 unified claim
```

---

# 六、不要破坏“页面匹配后才 claim”

共享 gate 不能让 greeting 提前 claim。

Greeting 顺序继续必须是：

```text
FineJob 开页
→ loading
→ probe
→ 页面匹配
→ 检查 shared gate
→ unified claim
```

不能改成：

```text
先占用/claim Action
→ 再找页面
```

共享 gate 只是执行互斥，不是 Action claim。

---

# 七、chat/resume 使用共享 gate 的位置

Chat/resume 当前已经从聊天页匹配可执行 Action。

正确顺序：

```text
聊天页有效
→ 找到明确 eligible Action
→ 检查/占用 shared gate
→ 指定 action_id claim
→ Preflight
→ dispatch
```

如果 claim 失败：

```text
释放 busy
```

如果 Preflight 明确不 dispatch：

```text
按当前 Action 状态完成后释放 busy
```

如果进入真实 dispatch：

```text
保持 busy
直到 FineJob 确认 result 已成功写回
```

---

# 八、greeting 使用共享 gate 的位置

Greeting 当前保持：

```text
page match
→ prepare_greeting_unified_bridge
```

在 unified claim 之前必须检查/占用同一个 shared gate。

如果：

```text
mapping missing
mapping ambiguous
unified terminal
claim fail
Preflight fail
```

且没有发生真实 side effect：

```text
本轮不得永久占住 busy
```

但本轮不要解决 legacy running 收口问题；那个属于下一轮 Sol 问题 5。

这里只保证 shared gate 本身不泄漏。

---

# 九、结果成功回写 FineJob 之后才进入 cooldown

三种真实动作统一要求：

```text
sender result
→ 第一时间 report FineJob
→ FineJob 确认本次结果已接收/更新
→ shared gate 从 busy 进入 cooldown
```

不能：

```text
sender result
→ 先 cooldown
→ 再 report
```

也不能：

```text
result report 失败
→ 释放 gate
→ 启动下一 Action
```

---

# 十、修复 chat result outbox 补传绕过 cooldown

Sol 已确认当前存在：

```text
首次 result report 失败
→ 写入 result outbox
→ 后续 processAccounts 补传成功
→ 没有进入 cooldown
→ 同一轮可以 claim 下一 Action
```

必须修成：

```text
补传 result 成功
→ shared gate 进入 cooldown
→ 当前 processAccounts 不得继续 claim
```

如果补传仍失败：

```text
保持 busy / waiting-for-result-sync
→ 不允许下一真实 Action
```

不得因为网络暂时失败而跳过 cooldown。

---

# 十一、修复 greeting legacy queue 为空时绕过 cooldown

Sol 已确认当前存在：

```text
greeting result 已回写
→ legacy queue 为空
→ greeting client 提前 return
→ 没有进入对 chat/resume 生效的共享 cooldown
→ chat heartbeat 可立即发送
```

必须修成：

```text
只要一个 greeting 真实执行结果已经成功回写 FineJob
→ shared gate 必须进入 cooldown
```

这个行为不能依赖：

```text
legacy queue 是否还有任务
```

cooldown 是“上一个真实动作完成后的节流”，不是“旧 greeting 队列还有没有任务”的条件。

---

# 十二、复用现有 cooldown 配置

当前已有 cooldown timer/配置必须继续作为时间来源。

Terra 可以让 shared gate 复用：

```text
现有 scheduleTaskCooldown
现有 cooldown duration
```

或者把现有 cooldown timer 的状态暴露给 shared gate。

禁止新增一个不同时间长度的第二套 cooldown。

最终全系统只能有一个对真实 Action 生效的 cooldown 状态。

---

# 十三、cooldown 期间所有入口都必须停

以下事件在 cooldown 中可以更新内存/同步数据，但不能开始新的真实 Action：

```text
chat heartbeat
task_queue WebSocket 更新
eligible Action 刷新
result outbox flush
页面 probe 结果
```

特别是：

```text
heartbeat
```

不能绕过 cooldown。

---

# 十四、修复 currentPageTaskId 旧值

Sol 已确认当前流程：

```text
结果同步
→ cooldown
→ startPageChecks
→ reportBossPageIdentity
```

但：

```text
currentPageTaskId
```

仍是刚刚完成的旧 task ID。

因此新队列中的其他任务被过滤掉。

---

# 十五、currentPageTaskId 什么时候清空

`currentPageTaskId` 在当前任务还执行时必须保留。

只有在：

```text
当前任务结果已经被 FineJob 成功同步
并准备进入“寻找下一任务”的阶段
```

才允许解除旧 task ID 约束。

建议真实顺序：

```text
result synced
→ 进入 cooldown
→ cooldown 完成
→ 清空 completed task 的 currentPageTaskId
→ startPageChecks
→ probe 当前页面
→ 根据当前 URL / probe / encryptJobId 匹配最新队列
```

如果当前实现更适合在 result synced 时清空，也必须证明：

```text
不会影响仍在处理的当前 Action
```

核心要求只有一个：

```text
下一轮 current-page match 不能再受已完成 task ID 限制。
```

---

# 十六、页面复匹配继续使用原 matcher

清空旧 task ID 后，不得放宽为“任意任务都匹配”。

继续使用：

```text
当前真实 URL
pathname
read-only probe
encryptJobId
现有 action identity
```

例如：

```text
当前仍是岗位 X
→ 只能匹配适用于岗位 X 的最新队列任务
```

如果当前页是聊天页：

```text
继续使用已有 /web/geek/chat 页面条件
```

不要新增 page kind matcher。

---

# 十七、当前页没有匹配任务才开新页面

正确顺序：

```text
cooldown 完成
→ 清掉旧 completed task ID 限制
→ probe 当前页面
→ 匹配最新队列
```

如果匹配成功：

```text
执行匹配 Action
→ 不调用 requestTaskPage
```

如果在现有匹配等待窗口内没有匹配：

```text
才 requestTaskPage
→ FineJob 打开下一目标页面
```

---

# 十八、必须补的离线测试

## A. greeting busy 阻止 chat claim

```text
greeting 已进入 unified claim / Preflight / dispatch
→ chat heartbeat
→ chat/resume claim 不发生
```

## B. chat busy 阻止 greeting unified claim

```text
chat/resume 正在 claim / Preflight / dispatch
→ greeting page match
→ greeting unified claim 不发生
```

不得改变 greeting 已完成的页面匹配结果。

## C. chat result 正常回写

```text
chat sender result
→ FineJob complete success
→ shared cooldown
→ cooldown 内 heartbeat 不 claim
→ cooldown 结束才可继续
```

## D. chat result outbox 补传

```text
首次 result report 失败
→ outbox
→ 不 claim 下一 Action
→ outbox 补传成功
→ 进入 shared cooldown
→ 同一 processAccounts 不 claim
```

## E. greeting result + legacy queue empty

```text
greeting result 成功回写
→ legacy queue empty
→ 仍进入 shared cooldown
→ chat heartbeat 不 claim
```

## F. cooldown 期间 queue update

```text
收到新 task_queue / eligible 更新
→ 可以保存队列
→ 不开始真实外发
```

## G. 当前页复匹配

```text
任务 A 完成
→ cooldown
→ currentPageTaskId(A) 被解除
→ 当前页面 probe 仍匹配任务 B
→ B 被选择
→ 不 requestTaskPage
```

## H. 当前页无匹配

```text
任务 A 完成
→ cooldown
→ 清旧 task ID
→ 当前页无最新 eligible match
→ 等待现有 match window
→ requestTaskPage
```

---

# 十九、本轮不得顺手修其他 Sol 问题

以下问题留到后续独立任务：

```text
问题 2：resume_list 创建阶段可达性
问题 3：greeting 网络异常 identity
问题 4：pause 未取消 unified Action
问题 5：greeting bridge 拒绝后 legacy running 收口
问题 6：succeeded 被后续 completion 降级
```

Terra 本轮不得扩大范围。

---

# 二十、测试要求

至少运行：

```text
FineJob client tests
chat coordinator tests
greeting bridge tests
action scheduler 相关 tests
background / integration tests（如果已有）
TypeScript typecheck
```

全部离线。

禁止真实 BOSS side effects。

---

# 二十一、完成输出

第一行必须输出：

```text
FINAL_FIX_ROUND1_COMPLETE
```

或者：

```text
FINAL_FIX_ROUND1_BLOCKED
```

然后完整输出：

```text
共享 gate：
greeting → chat 互斥：
chat → greeting 互斥：
result outbox：
greeting queue-empty cooldown：
currentPageTaskId：
当前页复匹配：
测试结果：
```

每项必须写：

```text
修改文件：
修改函数：
修改前行为：
修改后行为：
对应测试：
```

---

# 二十二、COMPLETE 条件

只有以下全部满足，才能输出：

```text
FINAL_FIX_ROUND1_COMPLETE
```

```text
greeting 与 chat/resume 使用同一个执行/cooldown gate
任一真实 Action busy 时另一循环不能 claim
result 成功回写后统一进入 cooldown
result 首次回写失败时不能继续 claim
outbox 补传成功后仍必须进入 cooldown
greeting legacy queue 为空不能跳过 cooldown
cooldown 完成后旧 currentPageTaskId 不再限制下一轮
当前页有匹配任务时不打开新页面
当前页无匹配任务时才 requestTaskPage
原 loading / matcher / sender / page open-close 没有被重写
```
