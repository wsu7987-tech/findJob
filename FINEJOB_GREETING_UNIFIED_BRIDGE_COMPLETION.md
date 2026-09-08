# FineJob：Greeting Legacy → Unified Bridge 收口任务

## 任务主要为了完成什么

本任务只完成一件事：

**把当前仍由旧 greeting 页面链执行的真实 greeting，在页面已经匹配成功之后，桥接到对应的 `fj_actions`，让 unified Action 真正负责 claim、Preflight 和 canonical result。**

本任务解决当前唯一的 greeting 缺口：

```text
旧页面链能正常打开岗位页、loading、probe、匹配和发送，
但 unified greeting 仍只是 shadow，
没有真正进入 sender 前的 claim / Preflight，
也没有在 sender 结果回报时成为业务结果权威。
```

本任务不重写原 greeting Executor。
本任务不修改 chat relation。
本任务不处理 resume snapshot。
本任务不修改 MQTT/Techwolf。
本任务不访问真实 BOSS。

---

# 一、必须保留的现有行为

Terra 必须完整保留：

```text
fj_automation_actions 旧导航兼容层
requestTaskPage
FineJob open_task_page
原前置 loading
page probe
URL / encryptJobId 匹配
currentPageTaskId（如果当前链仍需要）
原 greeting sender
sender 登录检查
页面 identity 检查
encryptJobId 检查
securityId 检查
contacted == false 检查
结果立即回报 FineJob
原 cooldown
close_page_after_completion
FineJob 实际打开/关闭页面
```

当前已经完成的 chat relation 代码也不得修改。

---

# 二、当前 greeting 真实链

当前仍是：

```text
legacy greeting task
→ requestTaskPage
→ FineJob 打开岗位页
→ loading
→ probe
→ encryptJobId 匹配
→ legacy match_task
→ legacy dispatch-started
→ default greeting sender
→ legacy complete_task
```

这个页面生命周期不重写。

---

# 三、目标链

修复后必须是：

```text
legacy greeting task
→ requestTaskPage
→ FineJob 打开岗位页
→ 原 loading
→ probe
→ URL / encryptJobId 匹配
→ legacy 页面匹配成功

→ 根据 legacy task id 找到唯一 unified greeting Action
→ 对这个明确 unified Action ID 做 conditional claim
→ unified greeting Preflight
→ Preflight passed

→ 原 greeting sender 自身安全检查
→ side effect
→ 插件第一时间回报 FineJob

→ unified Action 更新 canonical result
→ legacy task 同步兼容状态
→ 原 cooldown
→ 原页面关闭/保留逻辑
```

核心原则：

```text
旧链继续负责页面导航和页面匹配。
fj_actions 负责业务 claim、Preflight 和 canonical outcome。
```

---

# 四、必须复用 Stage 1 映射

当前已有：

```text
fj_actions.source_table = 'fj_automation_actions'
fj_actions.source_id = <legacy task id>
```

Terra 必须复用这个映射。

Terra 不得新建第二套 legacy→unified 映射表。

页面匹配成功以后：

```text
legacy task id
→ 查唯一对应 fj_actions greeting
```

必须满足：

```text
action_type = greeting
source_table = fj_automation_actions
source_id = legacy task id
```

如果查到：

```text
0 条
或
超过 1 条
```

则：

```text
fail closed
→ sender 不执行
→ 记录明确 mapping_missing / mapping_ambiguous
```

---

# 五、unified claim 必须发生在页面匹配之后

禁止：

```text
先 unified claim
→ 再开岗位页
```

必须：

```text
页面已经 loading 完成
→ probe
→ legacy 页面匹配成功
→ 找到 unified Action
→ 对明确 unified action_id claim
```

后端不得收到 legacy task 后再自行从 greeting 队列挑另一条 Action。

claim 必须针对：

```text
明确 action_id
```

并继续使用现有 conditional claim / lease / execution_epoch 并发保护。

---

# 六、legacy match_task 的职责不要扩大

`legacy match_task` 继续只表示：

```text
当前真实岗位页与这个 legacy navigation task 匹配成功
```

它不能继续作为最终业务执行授权。

真实 side effect 之前还必须通过：

```text
unified claim
→ unified greeting Preflight
```

如果 unified claim 失败：

```text
sender 不执行
```

如果 unified Preflight 失败：

```text
sender 不执行
```

---

# 七、greeting unified Preflight

Preflight 必须发生在：

```text
页面匹配成功
→ unified claim
→ greeting Preflight
→ sender
```

至少确认：

```text
统一 Action 仍然允许执行
当前岗位 identity 与 Action 一致
encryptJobId 一致
没有同岗位已 succeeded 的重复 greeting
没有同岗位结果 unknown 的 greeting
```

如果当前 Preflight 还能可靠拿到：

```text
contacted
securityId
登录状态
```

可以继续校验。

但原 sender 自己的最终安全检查必须保留，不能被 Preflight 替代。

---

# 八、移除 greeting 的错误账号前置依赖

greeting 不能因为：

```text
不存在聊天 session
不存在唯一 account_uid
```

而无法进入统一业务执行链。

如果当前 sender 或页面安全检查局部需要某个身份字段：

```text
可以保留该局部字段
```

但不能把：

```text
已有聊天账号/session
```

作为 greeting 被调度、映射或执行的前置条件。

---

# 九、新增最小 bridge execution identity

当前 sender/result 主要携带 legacy task identity。

为了让 sender 结果能够准确更新对应 unified Action，Terra 可以新增最小 bridge identity。

至少需要能够携带：

```text
unified_action_id
unified_execution_epoch
```

如果当前 unified completion 还要求：

```text
dispatch_token
lease_owner
```

则按现有协议最小携带。

bridge identity 必须和现有 legacy identity 并存。

例如：

```text
legacy_task_id
legacy_execution_epoch

unified_action_id
unified_execution_epoch
unified_dispatch_token（如现有接口需要）
```

具体字段名按项目现有类型风格。

---

# 十、bridge identity 的职责

legacy identity 继续负责：

```text
旧页面导航兼容
旧任务页面生命周期
legacy 状态兼容
```

unified identity 负责：

```text
unified claim
unified Preflight
dispatch authorization
canonical result update
```

sender 不需要知道业务调度逻辑。

sender 只透明携带当前执行 identity，并继续执行原安全检查。

---

# 十一、dispatch-started 顺序

必须是：

```text
legacy 页面匹配
→ unified claim
→ unified Preflight passed
→ unified dispatch-started
→ 原 sender
```

不能：

```text
legacy dispatch-started
→ unified Preflight
```

如果 legacy 表当前也需要 dispatch-started 兼容状态：

```text
可以在 unified dispatch-started 成功以后同步 legacy 兼容状态
```

unified 业务授权必须优先。

---

# 十二、结果回报

sender 执行后：

```text
插件必须第一时间回报 FineJob
```

FineJob 收到结果后必须：

```text
优先根据 unified bridge identity
更新对应 fj_actions canonical 状态
```

然后：

```text
同步 legacy task 的兼容状态
```

legacy 状态不能反向覆盖 unified canonical outcome。

---

# 十三、accepted / succeeded 语义继续保持

当前已经修复：

```text
greeting accepted 不直接 succeeded
```

本任务不得回退。

必须继续保持：

```text
transport / sender accepted
≠
业务 succeeded
```

如果没有可信平台确认：

```text
保持 accepted / unknown 的保守语义
```

不得因为 bridge 接通又把 accepted 写成 succeeded。

如果已有可信页面观察可以唯一确认：

```text
contacted == true
```

并且能与该 action 精确关联，可以继续使用现有 reconciliation。

本任务不要求新增真实平台确认机制。

---

# 十四、legacy completion 的兼容处理

Terra 必须检查当前：

```text
complete_task
_handle_task_completion_message
legacy lifecycle shadow sync
```

避免出现：

```text
sender 结果
→ unified complete
→ legacy complete
→ shadow sync 再次错误覆盖 unified
```

要求：

```text
unified canonical result 是业务权威
legacy completion 只做兼容同步
```

必须避免重复 completion。

---

# 十五、以下 unified Action 状态不能再次执行

如果页面匹配到 legacy task 后，对应 unified Action 已经是：

```text
succeeded
unknown
cancelled
superseded
stale
failed（如果当前业务规则禁止自动重试）
```

则旧 legacy task 不得继续触发 sender。

特别是：

```text
unknown
```

不能自动业务重试。

---

# 十六、失败时页面生命周期怎么处理

如果：

```text
mapping 找不到
unified claim 失败
Preflight 失败
dispatch token 无效
```

sender 不执行。

但是 Terra 不得重新设计页面生命周期。

后续：

```text
结果/阻断状态同步
→ 继续走当前原有 close/cooldown/page handling
```

具体按现有执行器已经存在的错误/终态路径复用。

---

# 十七、本轮禁止事项

本轮不得修改：

```text
chat relation service
chat 五分支
independent B planning
resume snapshot
resume sender
MQTT/Techwolf
requestTaskPage
open_task_page
loading timer
cooldown timer
URL/encryptJobId probe
页面关闭服务
具体 HR UI session
```

不得新增：

```text
account scheduler
page kind matcher
target_context matcher
第二套 greeting Executor
第二套页面状态机
```

---

# 十八、必须新增的离线测试

## 1. mapping

必须测试：

```text
legacy task id
→ 唯一 unified Action
```

以及：

```text
mapping missing
→ fail closed

mapping duplicated
→ fail closed
```

---

## 2. 页面匹配前不能 unified claim

必须证明：

```text
页面未匹配
→ unified claim 未调用
```

只有：

```text
legacy page match success
```

以后才能 claim。

---

## 3. claim / Preflight 顺序

必须断言：

```text
page match
→ unified claim
→ unified Preflight
→ unified dispatch-started
→ sender
```

任何一步失败：

```text
sender 不执行
```

---

## 4. sender 原安全检查继续存在

测试或现有测试必须继续覆盖：

```text
login
identity
encryptJobId
securityId
contacted == false
```

本任务不得删掉这些检查。

---

## 5. result bridge

必须测试：

```text
sender result
→ 带 unified_action_id / execution_epoch
→ unified canonical completion
→ legacy compatibility sync
```

并证明：

```text
legacy sync 不会把 unified unknown/accepted 错误提升为 succeeded
```

---

## 6. 重复执行保护

必须覆盖：

```text
unified succeeded
→ legacy 不再发送

unified unknown
→ legacy 不再发送

unified cancelled/superseded/stale
→ legacy 不再发送
```

---

## 7. 原 lifecycle 不回归

至少保留/运行现有测试证明：

```text
page open
loading
probe/match
result report
cooldown
close_page_after_completion
```

没有被本 bridge 修改。

---

# 十九、测试要求

至少运行：

```text
backend greeting executor 相关 tests
action_scheduler tests
action_store Stage 1 tests
finejob-client tests
default greeting sender tests
新增 greeting bridge tests
TypeScript typecheck
```

禁止真实 BOSS。

---

# 二十、完成输出

第一行必须输出：

```text
GREETING_UNIFIED_BRIDGE_COMPLETE
```

或者：

```text
GREETING_UNIFIED_BRIDGE_BLOCKED
```

然后继续输出：

```text
legacy→unified mapping：
claim 位置：
Preflight 位置：
bridge execution identity：
result completion：
legacy compatibility sync：
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

# 二十一、什么时候才能 COMPLETE

必须全部满足：

```text
旧页面导航/匹配链没有被重写
页面匹配后才能找到并 claim unified Action
unified greeting Preflight 真正在 sender 前
sender 原安全检查继续保留
结果通过 bridge identity 更新 unified canonical status
legacy 只做兼容同步
accepted 不直接 succeeded
unknown 不自动重试
原 loading/cooldown/page close 行为没有回归
```

任何一项没有真实接通：

```text
GREETING_UNIFIED_BRIDGE_BLOCKED
```
