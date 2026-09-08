# FineJob：Resume Preflight Snapshot 收口任务

## 任务主要为了完成什么

本任务只完成一件事：

**把 `resume_send` 的“最新附件列表校验”从 sender 内部提前到真正的 Preflight 阶段，确保 `dispatch_started` 之前已经确认用户原来选定的 `encryptResumeId + filename` 仍然有效。**

本任务解决当前唯一剩余的 resume 执行顺序问题：

```text
当前：
claim
→ backend preflight
→ dispatch_started
→ sender
→ sender 内 listResumeAttachments
→ 校验原简历
→ side effect
```

需要改成：

```text
claim
→ 只读获取最新 resume snapshot
→ backend preflight 精确校验
→ preflight passed
→ dispatch_started
→ sender
→ sender 内最后一道防御性校验
→ side effect
```

本任务不修改 chat relation。
本任务不修改 greeting bridge。
本任务不修改页面生命周期。
本任务不修改 MQTT/Techwolf。
本任务不访问真实 BOSS。

---

# 一、必须保留的当前工作区成果

Terra 必须保留：

```text
chat relation 五分支完整链
independent B preserve-existing planning
A/B waiting barrier
greeting legacy→unified bridge
greeting unified claim / Preflight / canonical result
指定 action_id claim
page_opened 后 loading
cooldown gate
cooldown 后先 probe 当前页
resume encryptResumeId + filename 固定校验
resume outbound reconciliation
accepted != succeeded
unknown blocker
send_enabled fail-closed
dry-run
```

不得回退。

---

# 二、当前 resume 真实链

当前已经存在：

```text
unified resume Action
→ claim
→ backend Preflight
→ dispatch_started
→ Main World BossChatSender.send
→ sender 内 listResumeAttachments
→ 精确验证 encryptResumeId + filename
→ side effect
→ result report
→ outbound reconciliation
```

当前问题只有：

```text
最新附件验证发生得太晚
```

也就是发生在：

```text
dispatch_started
```

之后。

---

# 三、目标执行顺序

修复后必须形成：

```text
聊天页已满足现有执行条件
→ 明确 resume Action 已 claim

→ Background 发起只读 resume snapshot request
→ Main World 调用现有 listResumeAttachments
→ Main World 返回 snapshot result

→ Background 把 snapshot 提交 backend Preflight
→ backend 精确验证 Action 已保存的 encryptResumeId + filename
→ backend Preflight passed

→ dispatch_started
→ 原 sender
→ sender 内可以继续保留最后一次防御性附件校验
→ side effect

→ 插件第一时间 report FineJob
→ accepted 保守处理
→ 可信 outbound observation/reconciliation 才 succeeded
→ cooldown
```

---

# 四、只读 snapshot 通道必须独立于发送结果通道

Terra 必须新增一个独立的只读 command/result 类型。

可以按当前协议风格命名，例如：

```text
BOSS_RESUME_SNAPSHOT_REQUEST
BOSS_RESUME_SNAPSHOT_RESULT
```

实际名称按项目现有 convention。

必须包含：

```text
request_id
action_id
```

结果至少包含：

```text
request_id
action_id
attachments
observed_at
error
```

每个 attachment 只需要：

```text
encryptResumeId
filename
```

不得返回 PDF 正文。

---

# 五、不得复用 Action completion result

snapshot 结果不能复用：

```text
ChatSendExecutionResult
MainWorldExecutionResult
Action completion
```

因为 snapshot 不是发送结果。

snapshot 返回不能触发：

```text
complete_action
accepted
failed
unknown
succeeded
```

它只能作为：

```text
Preflight 输入
```

---

# 六、Background 等待逻辑

Background 必须能够：

```text
发送 snapshot request
→ 按 request_id 等待对应 result
```

需要有明确 timeout。

以下情况全部 fail closed：

```text
timeout
Main World error
返回格式非法
action_id 不匹配
request_id 不匹配
```

fail closed 的含义：

```text
backend Preflight 不通过 / waiting / blocked
dispatch_started 不发生
side effect 不发生
```

不要自动重试业务发送。

---

# 七、复用现有 Main World 附件读取能力

当前已有：

```text
BossChatSender.listResumeAttachments
```

Terra 必须复用它。

不要再写第二套：

```text
resume list API
附件抓取器
简历 selector
```

Main World snapshot handler 只负责：

```text
调用 listResumeAttachments
→ 提取 encryptResumeId + filename
→ 返回只读结果
```

---

# 八、backend Preflight 必须使用最新 snapshot

Preflight 请求需要携带最新 snapshot。

可以最小扩展现有 snapshot/schema，例如：

```text
resume_list_snapshot
resume_list_observed_at
```

具体字段名按当前 schema 风格。

backend `_resume_preflight` 必须：

```text
读取 Action 已保存：
encrypt_resume_id
resume_filename

读取最新 snapshot：
attachments
```

然后精确查找：

```text
encryptResumeId 完全一致
+
filename 完全一致
```

---

# 九、验证结果

## 原简历仍存在

```text
snapshot 中存在：
相同 encryptResumeId
+
相同 filename
→ Preflight passed
```

## 原 ID 不存在

```text
Preflight fail
→ 不 dispatch
```

## ID 存在但 filename 不匹配

```text
Preflight fail
→ 不 dispatch
```

## filename 相同但 ID 不同

```text
Preflight fail
→ 不 dispatch
```

---

# 十、绝对不能自动换简历

执行阶段已经保存：

```text
encryptResumeId
resume_filename
```

这两个字段代表用户/创建阶段已经确定的那份简历。

Preflight 只能：

```text
验证它还在不在
```

不能：

```text
原简历失效
→ 选列表第一份
```

不能：

```text
重新根据数量自动选一份
```

`0/1/多份` 的选择规则属于创建/选择阶段。

执行 Preflight 不重新选择。

---

# 十一、sender 内原校验继续保留

当前 sender 已经有：

```text
发送前 listResumeAttachments
→ encryptResumeId + filename 精确校验
```

本任务不要删除它。

修复后形成两层：

```text
第一层：
dispatch_started 前 backend Preflight 使用最新 snapshot

第二层：
sender 真正 side effect 前最后再防御性校验
```

这是故意的双层保护。

---

# 十二、HR 真人回复条件继续保留

现有 resume Preflight 已经正确检查：

```text
真实 recruiter_text
```

平台事件不能算 HR 真人回复。

这些继续保留：

```text
platform_ad
resume_viewed
resume_read_receipt
附件状态
系统事件
```

不能满足 resume_send 的真人回复条件。

---

# 十三、重复发送保护继续保留

必须继续阻止：

```text
同 HR 已有 succeeded resume_send
同 HR 已有 unknown resume_send
```

不得因为新增 snapshot 通道而改变。

---

# 十四、结果语义继续保持

继续保持：

```text
transport accepted != succeeded
```

`resume_send` 只有可信 outbound observation / reconciliation 才能提升：

```text
succeeded
```

如果不能确认：

```text
accepted / unknown
```

保持保守。

不得自动业务重试。

---

# 十五、必须新增的离线测试

## 1. snapshot command/result

测试：

```text
Background 发 request
→ Main World 收到只读 snapshot command
→ 调用 listResumeAttachments
→ 返回独立 snapshot result
```

必须证明：

```text
snapshot result 不触发 Action completion
```

---

## 2. strict order

测试必须断言顺序：

```text
claim
→ snapshot request
→ snapshot result
→ backend Preflight
→ Preflight passed
→ dispatch_started
→ sender
```

不得只测试独立函数。

---

## 3. timeout

```text
snapshot timeout
→ Preflight 不通过
→ dispatch_started 不发生
→ sender 不调用
```

---

## 4. snapshot error

```text
Main World snapshot error
→ dispatch_started 不发生
→ side effect 不发生
```

---

## 5. exact resume match

分别测试：

```text
ID + filename 都匹配 → passed
ID 缺失 → blocked
ID 匹配 filename 不同 → blocked
filename 匹配 ID 不同 → blocked
```

---

## 6. 不自动换简历

snapshot 里存在其他有效简历，但原简历不存在：

```text
→ blocked
→ 不选择其他 resume
```

---

## 7. sender 最后校验仍存在

必须保留现有 sender 测试：

```text
真正 side effect 前
仍再次精确检查 encryptResumeId + filename
```

---

## 8. result semantics

继续测试：

```text
transport accepted → 不直接 succeeded
可信 resume outbound observation → succeeded
unknown → 不自动 retry
```

---

# 十六、本轮禁止事项

不得修改：

```text
chat relation
chat 五分支
independent B planning
greeting bridge
greeting sender
requestTaskPage
open_task_page
loading
cooldown
页面关闭
URL/encryptJobId matcher
MQTT/Techwolf
具体 HR UI session
```

不得新增：

```text
第二套附件 API
第二套 sender
account scheduler
page kind matcher
新的页面生命周期
```

---

# 十七、测试要求

至少运行：

```text
boss chat sender tests
chat coordinator tests
action scheduler tests
resume 相关 backend tests
新增 resume snapshot tests
TypeScript typecheck
```

如果修改 message/content/background 类型，运行对应现有扩展测试。

禁止真实 BOSS。

---

# 十八、完成输出

第一行必须输出：

```text
RESUME_PREFLIGHT_SNAPSHOT_COMPLETE
```

或者：

```text
RESUME_PREFLIGHT_SNAPSHOT_BLOCKED
```

然后继续输出：

```text
snapshot command：
snapshot result：
Background wait：
backend Preflight：
dispatch 顺序：
sender 最终校验：
result semantics：
测试结果：
```

每一项写：

```text
修改文件：
修改函数：
修改前行为：
修改后行为：
对应测试：
```

---

# 十九、什么时候才能 COMPLETE

必须全部满足：

```text
snapshot 有独立 request/result 通道
snapshot 复用现有 listResumeAttachments
snapshot 不触发 Action completion
最新 snapshot 在 dispatch_started 前进入 backend Preflight
backend 精确验证原 encryptResumeId + filename
snapshot 失败时 fail closed
原简历失效时不自动换简历
sender 最终防御校验继续保留
accepted 不直接 succeeded
可信 observation 才 succeeded
现有 chat/greeting/page lifecycle 没有回归
```

任何一项没有真实接通：

```text
RESUME_PREFLIGHT_SNAPSHOT_BLOCKED
```
