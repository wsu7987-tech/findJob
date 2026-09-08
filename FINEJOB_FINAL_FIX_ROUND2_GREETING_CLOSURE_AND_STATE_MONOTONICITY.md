# FineJob：Final Fix Round 2 — Greeting 异常结果 + Bridge 收口 + Canonical 状态单调性

## 任务主要为了完成什么

本任务只修 Sol 最终审查中的三个相关问题：

```text
问题 3：
greeting 网络异常结果缺少 unified execution identity，
导致结果回退到 legacy completion。

问题 5：
greeting bridge 拒绝后，
legacy task 已经进入 running/leased，但没有进入明确终态或安全回队。

问题 6：
可信 outbound observation 已经把 unified Action 提升为 succeeded，
后续 sender completion 仍可能把 succeeded 降级成 accepted / unknown / failed。
```

这三个问题都属于：

```text
执行结果身份
→ bridge 异常收口
→ canonical 状态机
```

所以本轮一起修。

本任务不处理：

```text
resume_list 创建阶段可达性（问题 2）
pause 未取消 unified Action（问题 4）
```

本任务不修改：

```text
chat relation
independent B planning
共享 gate
cooldown
currentPageTaskId
页面复匹配
resume snapshot
MQTT/Techwolf
页面生命周期
```

---

# 一、必须保留 Round 1 已完成内容

当前工作区已经完成并通过：

```text
Background 唯一 shared action gate
greeting 与 chat/resume 真实外发互斥
result outbox 补传后进入 shared cooldown
greeting queue empty 不再绕过 cooldown
cooldown 后清理 completed currentPageTaskId
当前页先 probe/match，匹配不到才 requestTaskPage
```

本轮不得回退这些行为。

---

# 二、问题 3：所有 greeting sender 结果必须携带 unified execution identity

## 当前问题

正常 greeting sender 结果已经携带：

```text
unifiedActionId
unifiedExecutionEpoch
```

但网络异常 / catch 分支漏掉这些字段。

结果导致：

```text
unified dispatch
→ sender 网络异常
→ result 缺 unified identity
→ backend complete_task 无法走 unified-first completion
→ 回退 legacy completion
→ unified canonical 状态漂移
```

---

# 三、修复要求：identity 必须从执行开始就绑定

Terra 必须检查：

```text
MainWorldExecutionTask
MainWorldExecutionResult
executeDefaultGreeting
submitExecutionResult
complete_task
```

要求：

```text
一旦 greeting 已通过 unified claim + Preflight + dispatch_started，
本次执行的 unified identity 就必须固定，
并在所有结果分支原样返回。
```

包括：

```text
success
accepted
blocked
failed
unknown
network error
fetch exception
timeout
sender internal exception
```

都必须携带同一个：

```text
unifiedActionId
unifiedExecutionEpoch
```

以及当前 unified completion 真正需要的：

```text
dispatch token / lease owner / execution token
```

如果现有协议已经有等价字段，直接复用。

不得只在 happy path 填 identity。

---

# 四、MainWorldExecutionResult 的类型要求

如果 greeting bridge 已经要求 unified identity 才允许真实 sender 执行，

则 sender 返回结果类型应该反映这个事实。

优先方案：

```text
真实 greeting dispatch 分支：
unified identity 必须存在
```

不要让 catch 返回一个“缺 unified identity 但还能提交”的宽松结果。

如果 TypeScript 类型暂时必须保留 optional 兼容旧测试任务，则：

```text
真实 unified greeting 执行路径必须通过单独 typed helper / runtime guard 保证 identity 完整
```

---

# 五、问题 5：bridge reject 后 legacy running 必须原子收口

## 当前问题

当前流程：

```text
legacy page match
→ match_task 把 legacy task 改为 running/leased
→ prepare_greeting_unified_bridge
→ bridge reject
→ sender 不执行
→ plugin 只清本地 dispatchingTaskId
→ backend legacy task 继续 running
```

这会让队列卡住。

---

# 六、bridge reject 必须按 reason 收口

Terra 必须检查 `prepare_greeting_unified_bridge` 返回的拒绝原因。

至少区分：

```text
mapping_missing
mapping_ambiguous
unified_terminal
unified_claim_failed
preflight_blocked
preflight_waiting
preflight_uncertain
```

具体 reason 名称按当前代码实际值。

每一种 bridge reject 都必须让：

```text
legacy task
+
unified Action（如果存在）
```

进入一致状态。

---

# 七、哪些情况可以安全回队，哪些不能

必须遵守：

```text
只有确认没有发生真实 side effect，
并且该拒绝是临时可恢复条件，
才允许安全回队。
```

例如：

```text
temporary claim contention
短暂 lease conflict
明确 waiting 且业务仍允许稍后继续
```

可以按现有状态机进入：

```text
queued / waiting
```

但以下情况不得回队后自动重试：

```text
unified unknown
unified succeeded
cancelled
superseded
stale
blocked terminal
mapping ambiguous
mapping missing（如果代表数据一致性错误）
```

其中：

```text
unknown
```

继续严格禁止自动业务重试。

---

# 八、legacy 兼容状态必须明确

bridge reject 后，legacy task 不得停留：

```text
running
leased
dispatching
```

除非真实 sender 仍在运行。

后端必须在同一收口函数/事务中：

```text
更新 unified（如需要）
→ 更新 legacy compatibility state
→ 释放 lease / running 标志
```

避免：

```text
插件认为没执行
但 FineJob 认为仍在执行
```

---

# 九、问题 6：canonical succeeded 必须单调

## 当前问题

存在竞态：

```text
sender 开始
→ 远端 outbound echo 先到
→ observe_outbound_result
→ Action = succeeded

稍后本地 publish callback / sender completion 返回
→ complete_action(accepted/unknown/failed)
→ 把 succeeded 覆盖掉
```

这是错误的。

---

# 十、统一状态优先级

Terra 必须让 canonical 状态遵守：

```text
可信远端 succeeded
>
本地 transport accepted
>
本地 sender unknown / failed
```

但这里不是简单全局排序。

核心规则：

```text
如果当前 Action 已经由可信 outbound observation 确认 succeeded，
后续同 execution epoch 的本地 completion
不得把它降级。
```

---

# 十一、complete_action 必须做条件更新

`complete_action` 不得继续无条件覆盖当前状态。

必须至少检查：

```text
action_id
execution_epoch
当前 status
当前 dispatch token / owner（如现有状态机要求）
```

推荐行为：

```text
当前 status = succeeded
→ completion 幂等返回当前 succeeded
→ 不降级

当前 status = dispatching / accepted
且 epoch 匹配
→ 才允许按本地 completion 更新

epoch 不匹配
→ 拒绝 stale completion
```

如果当前已经存在更强 canonical evidence 字段，优先复用。

---

# 十二、observe_outbound_result 也必须保持幂等

可信 outbound echo：

```text
第一次 → succeeded
重复 echo → succeeded 幂等
```

不得：

```text
重复写出新的 execution epoch
创建第二次业务 completion
```

---

# 十三、适用范围

问题 6 不只检查 greeting。

Terra 必须确认：

```text
chat_message
resume_send
greeting
```

只要共用 `complete_action / observe_outbound_result`，都受到同一个 canonical 单调性保护。

不要只给 resume 特判。

---

# 十四、accepted / unknown 语义继续保持

本轮不得改变：

```text
transport accepted != succeeded
```

也不得把：

```text
network exception
```

直接当 succeeded。

正确行为仍然是：

```text
可信远端 echo / observation
→ succeeded

本地已发但无法确认
→ accepted / unknown

unknown
→ 不自动 retry
```

---

# 十五、必须新增的离线测试

## A. greeting 网络异常 identity

覆盖：

```text
unified greeting 已 dispatch_started
→ sender fetch/network exception
→ result 仍带 unifiedActionId + unifiedExecutionEpoch
→ backend 走 unified-first completion
→ 不走纯 legacy completion
```

---

## B. greeting 其他异常路径 identity

至少覆盖：

```text
sender internal throw
timeout
unknown result
```

都必须保留 unified identity。

---

## C. bridge mapping missing

```text
legacy task 已 running
→ mapping missing
→ sender 不执行
→ legacy 不保持 running
→ 状态进入明确 terminal / blocked
```

---

## D. bridge mapping ambiguous

同上：

```text
不 sender
不 running 卡死
```

---

## E. bridge unified terminal

例如：

```text
unified succeeded
unified unknown
```

必须：

```text
legacy 收口
→ 不重新发送
```

---

## F. bridge claim fail / temporary conflict

如果当前业务设计允许安全回队：

```text
legacy 不 running 卡死
→ 回到明确 queued/waiting
```

如果当前代码判为终态：

```text
也必须有明确状态
```

测试要与实际 reason 语义一致。

---

## G. outbound observation 早于 completion

关键竞态测试：

```text
Action dispatching
→ observe_outbound_result(succeeded)
→ complete_action(accepted)
→ 最终仍 succeeded
```

再测：

```text
observe succeeded
→ complete unknown
→ 最终仍 succeeded
```

以及：

```text
observe succeeded
→ complete failed
→ 最终仍 succeeded
```

---

## H. stale execution epoch completion

```text
Action 已进入新 execution epoch
→ 旧 epoch completion 到达
→ 不允许覆盖当前 canonical state
```

---

## I. duplicate outbound echo

```text
同一 Action 同一可信 outbound echo 重复到达
→ succeeded 幂等
→ 不产生重复业务动作
```

---

# 十六、本轮禁止顺手修改的问题

本轮不要处理：

```text
问题 2：resume_list 创建阶段不可达
问题 4：pause/resume unified Action 取消
```

也不要重新打开：

```text
chat relation
resume snapshot
shared gate
current page reuse
loading/cooldown
页面开关
```

---

# 十七、测试要求

至少运行：

```text
greeting bridge tests
default greeting sender tests
boss executor completion tests
action scheduler tests
outbound reconciliation tests
Stage 1 action store tests
TypeScript typecheck
```

如果相关现有完整扩展测试可运行，也一起运行。

全部离线。

禁止真实 BOSS side effects。

---

# 十八、完成输出

第一行必须输出：

```text
FINAL_FIX_ROUND2_COMPLETE
```

或者：

```text
FINAL_FIX_ROUND2_BLOCKED
```

然后完整输出：

```text
greeting exception identity：
bridge reject closure：
mapping missing / ambiguous：
terminal / unknown：
canonical succeeded monotonicity：
stale epoch：
duplicate outbound observation：
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

# 十九、COMPLETE 条件

只有以下全部满足，才能输出：

```text
FINAL_FIX_ROUND2_COMPLETE
```

```text
所有真实 greeting 结果分支都保留 unified execution identity
网络异常不能回退纯 legacy completion
bridge reject 后 legacy 不再卡 running/leased
mapping missing/ambiguous fail closed 且有明确收口
unified succeeded/unknown 不会被 legacy 自动重发
trusted outbound succeeded 不会被后续 completion 降级
stale epoch completion 不会覆盖当前执行
duplicate outbound echo 幂等
accepted != succeeded 继续成立
unknown 不自动 retry
Round 1 shared gate / cooldown / current page reuse 不回归
