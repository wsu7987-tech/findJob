# FineJob UI 收尾任务

## 目标

当前 Executor / Scheduler / Preflight 主链已经通过最终验收。

本轮开始进入 UI 收尾。

UI 的职责只有：

```text
把当前后端已经存在的真实状态、操作和等待原因正确展示出来
```

UI 不重新设计 Executor。
UI 不修改业务状态机。
UI 不自己决定 Action 是否可执行。
UI 不绕过后端 Preflight / claim / shared gate。

---

# 一、本轮 UI 必须覆盖的业务对象

## 1. Action

统一使用当前 `fj_actions` 作为业务状态权威。

UI 至少展示：

```text
action_type
status
waiting / blocked reason
session_sequence
created_at
updated_at
```

真实 Action 类型：

```text
greeting
chat_message
resume_send
```

legacy greeting 状态只能作为兼容/调试信息。

UI 不得把：

```text
legacy greeting status
```

展示成第二套业务 Action 状态。

---

# 二、Action 状态展示

至少正确识别当前已有状态：

```text
waiting
awaiting_confirmation
queued
claimed
preflighting
dispatching
accepted
unknown
succeeded
blocked
cancelled
superseded
stale
failed
```

不要简单全部显示原始英文。

需要给用户可理解的中文状态，例如：

```text
waiting → 等待条件满足
awaiting_confirmation → 等待确认
queued → 待执行
claimed → 已领取
preflighting → 执行前检查
dispatching → 正在执行
accepted → 已发出，等待平台确认
unknown → 结果无法确认
succeeded → 已确认完成
blocked → 已阻止
cancelled → 已取消
superseded → 已被新版本替代
stale → 已失效
failed → 执行失败
```

`accepted` 不能显示成“成功”。

`unknown` 必须明显区别于失败和成功。

---

# 三、Chat UI

## 1. 当前会话状态

展示：

```text
active
paused
manual takeover / 人工接管
```

如果会话暂停：

```text
UI 明确显示自动动作已暂停
```

恢复以后不能把暂停前已经取消的旧 Action 当成当前待发动作展示。

---

## 2. 回复草稿 / Action

对于待回复：

```text
显示当前 draft
显示是否需要确认
显示当前 Action 状态
显示关联的 inbound message
```

如果存在 replacement：

```text
A → superseded
A' → 当前有效版本
```

UI 默认突出 A'。

旧 A 可以折叠显示为：

```text
已被新消息替代
```

---

## 3. 独立新问题 B

必须能表达：

```text
Reply-A 已准备
但因独立新问题 B 尚未准备完成而等待
```

UI 不要显示成：

```text
A 被取消
```

而应显示：

```text
A：等待后续回复 B 准备完成
B：待生成 / 待确认 / 已准备
```

当 B ready：

```text
显示执行顺序 A → B
```

不要把 A+B 合并成一条消息。

---

## 4. classification_uncertain

如果 relation decision 为：

```text
classification_uncertain
```

UI 必须明确：

```text
系统无法可靠判断新消息与当前回复的关系
自动发送已暂停
```

不能只显示“waiting”。

---

# 四、Resume UI

## 1. 简历列表

UI 必须支持：

```text
刷新附件简历
加载中
读取失败
0 份
1 份
多份
```

当前 `resume_list` 是只读 helper。

UI 不把它展示为：

```text
正在发送
accepted
unknown
succeeded
```

---

## 2. 选择规则

必须遵守后端真实规则：

```text
0 份：
不能创建 resume_send

1 份：
自动选择唯一简历

多份：
必须用户明确选择
```

UI 显示选中简历：

```text
filename
```

可以在调试/详情区域显示：

```text
encryptResumeId
```

不要把 encryptResumeId 作为用户主要识别信息。

---

## 3. resume_send

UI 展示：

```text
目标 HR / session
选中的 filename
Action 状态
等待原因
```

如果 Preflight 因最新附件列表不匹配被阻止：

```text
明确提示：
原先选择的简历已不存在或文件信息已变化，请重新选择
```

UI 不自动替用户切换简历。

---

# 五、Greeting UI

Greeting UI 只展示 unified Action 的业务状态。

可以展示：

```text
岗位
公司
Action 状态
等待/阻止原因
最近执行结果
```

不要让用户看到两套平行状态：

```text
legacy running
unified queued
```

如果确实需要 debug 信息：

```text
放入“技术详情 / 调试信息”
```

不要放主界面。

---

# 六、Executor 状态区

UI 建议增加一个轻量执行状态区，只展示真实运行状态：

```text
Idle
Executing
Cooldown
Paused
Disconnected
```

可以显示：

```text
当前 Action 类型
当前 Action ID
当前执行阶段
cooldown 剩余时间（如果现有 API 提供）
```

UI 只展示。

UI 不自己改变：

```text
shared gate
claim
Preflight
dispatch
cooldown
```

---

# 七、队列展示

UI 需要展示 FineJob 当前 ordered eligible queue。

至少包含：

```text
顺序
Action 类型
目标
状态
waiting reason
session_sequence（chat/resume）
```

UI 排序必须使用后端已经给出的顺序。

不得在前端重新发明 priority 算法。

---

# 八、错误与阻止原因

不要只显示：

```text
blocked
failed
unknown
```

优先映射后端 reason code。

至少针对当前真实场景给明确文案：

```text
classification_uncertain
→ 无法确认新消息与当前回复的关系

resume_attachment_invalid
→ 已选简历已失效，请重新选择

snapshot_timeout
→ 无法读取最新附件列表

account_identity_required
→ 账号身份信息不足

identity_mismatch
→ 当前页面与目标任务不匹配

unknown
→ 已尝试执行，但平台结果无法确认，请人工核对
```

如果后端返回未知 reason：

```text
显示原 reason code
```

不要吞掉。

---

# 九、用户可操作按钮

UI 只能调用当前后端已有、明确安全的业务接口。

允许的典型操作：

```text
确认回复
修改回复
暂停自动动作
恢复自动动作
人工接管
刷新简历列表
选择简历
查看 Action 详情
```

不得增加：

```text
“强制发送”
“跳过 Preflight”
“忽略 unknown”
“强制重试”
“直接 claim”
```

除非后端当前已经有明确安全接口和业务定义。

---

# 十、实时刷新

如果当前已有 websocket / polling 状态接口：

```text
优先复用
```

UI 至少要及时反映：

```text
Action status
session paused
draft/replacement
resume list
selected resume
executor busy/cooldown
unknown/blocked reason
```

不要为了 UI 新建第二套实时同步协议。

---

# 十一、视觉层级

主页面优先顺序：

```text
1. 当前需要用户处理的内容
2. 当前正在执行的 Action
3. 等待中的 Action
4. 最近完成/异常
5. 调试详情
```

不要把几十个底层字段平铺。

用户首先应该一眼看懂：

```text
现在系统在做什么
为什么没发
我现在需要做什么
```

---

# 十二、本轮不允许修改的后端核心

除非 UI 对接时发现明确“字段缺失导致 UI 无法表达真实状态”的接口缺口，否则不要修改：

```text
action_scheduler
shared gate
cooldown
page lifecycle
chat relation
greeting bridge
resume snapshot
MQTT/Techwolf
claim / Preflight / dispatch 规则
session_sequence
unknown blocker
```

如果发现接口缺口：

```text
只补读取/展示所需字段
不要改变执行逻辑
```

---

# 十三、本轮测试规则

只跑 UI 和 UI 对接直接相关的定向测试。

禁止：

```text
backend 全量 pytest
extension 全量 test
desktop 全量 test
无关模块测试
```

测试前必须说明：

```text
这条测试验证什么 UI 风险
```

只验证：

```text
状态映射
按钮调用
pause/resume UI
draft/replacement
resume list/selection
Action detail
executor state
错误 reason 展示
```

---

# 十四、完成输出

第一行：

```text
FINEJOB_UI_FINISH_COMPLETE
```

或：

```text
FINEJOB_UI_FINISH_BLOCKED
```

然后报告：

```text
1. 修改的 UI 页面/组件
2. Action 状态展示
3. Chat UI
4. Resume UI
5. Greeting UI
6. Executor/队列状态
7. pause/manual takeover
8. 错误 reason 展示
9. 只读后端接口补充（如果有）
10. 定向测试结果
11. 仍存在的 UI blocker
```

不要扩展到无关产品功能，不要删掉原本存在的功能。

---

## 本轮完成记录

已完成 UI 收尾的最小实现：

- 自动代聊详情单独读取并展示统一 `fj_actions`，保留附件 `resume_list` helper 的只读语义。
- Action 主界面展示中文状态、等待/阻止原因、会话顺序、关联 HR 消息、创建和更新时间；替代版本收纳在折叠区。
- 会话暂停、人工接管、`classification_uncertain`、简历附件读取与简历发送状态均使用后端真实字段显示。
- 运行状态页展示统一 Action 队列，并区分“插件连接已中断”与“已断开，需重新配对”。应用启动不再主动触发连接检查。
- 仅补充聊天详情、执行器状态和运行看板所需的只读展示字段，未改变执行、领取、预检或调度规则。

定向验证通过：

- `apps/desktop/src/renderer/services/fineJobActionPresentation.test.ts`
- `apps/desktop/src/renderer/services/fineJobExecutorPresentation.test.ts`
- `apps/desktop/src/renderer/pages/fine-job/BossChat.test.ts`
- `backend/tests/api/test_fine_job_boss_executor_api.py -k revoked_pairing_for_ui`
- `backend/tests/api/test_fine_job_boss_chat_api.py -k session_detail_exposes_unified_action_fields_for_ui`
