# FineJob：Cutover 后本地工作区执行链完整性审计

## 任务目的

本任务主要完成两件事：

1. **确认当前本地未提交代码在执行 `FINEJOB_SCHEDULER_PREFLIGHT_CUTOVER` 后，是否破坏了原本已经能工作的页面执行链。**
2. **准确定位当前 bug，判断后续应该局部修复，还是只恢复被破坏的原代码。**

本任务不重新设计执行器。
本任务不重做任务排序。
本任务不重做执行前检查。
本任务不修改代码。
本任务不回退代码。

---

# 一、当前 Git 状态

当前情况是：

```text
HEAD
= 执行 FINEJOB_SCHEDULER_PREFLIGHT_CUTOVER 之前的已提交版本

当前工作区
= 已经执行完 FINEJOB_SCHEDULER_PREFLIGHT_CUTOVER
+ 当前仍然存在 bug
+ 所有本次改动尚未提交
```

Codex 必须以：

```text
HEAD
vs
当前未提交工作区
```

作为本次检查基础。

Codex 可以使用：

```text
git status
git diff HEAD
git diff --cached HEAD
未跟踪文件检查
```

Codex 不得执行：

```text
git reset
git restore
git checkout 覆盖工作区
git clean
git stash
git commit
```

当前未提交代码必须完整保留。

如果工作区中还混有 cutover 之前就存在的未提交改动，Codex 必须明确指出，不能把所有 diff 都自动认定为本次 cutover 产生。

---

# 二、原执行链是本次审计的基准

用户要求保持的执行链是：

```text
FineJob 维护并同步最新执行队列
→ 插件收到执行队列
→ 插件处于可以继续工作的状态

如果 FineJob 刚打开了任务页面：
→ 插件先完成原本的前置 loading
→ 前置 loading 完成
→ 插件读取当前页面
→ 插件使用原有 URL / 原有任务标识匹配执行队列

匹配成功：
→ FineJob 锁定该任务
→ 执行前检查
→ 真正执行
→ 插件第一时间把执行结果报告给 FineJob
→ FineJob 更新任务状态和执行队列
→ 插件进入原本的执行后 cooldown/loading
→ 原有页面关闭配置决定页面是否关闭

如果页面没有关闭：
→ cooldown/loading 完成
→ 插件使用当前页面重新匹配 FineJob 最新执行队列
→ 当前页面有匹配任务：继续执行
→ 当前页面没有匹配任务：插件请求 FineJob 打开新的任务页面

FineJob 打开新页面：
→ 前置 loading
→ loading 完成
→ 再次匹配
→ 循环
```

---

# 三、FineJob 和插件的责任边界

## FineJob 负责

FineJob 负责：

```text
维护真实外发任务
维护任务优先顺序
同步执行队列
真正打开 BOSS 页面
真正关闭 BOSS 页面
锁定任务
更新任务状态
接收插件执行结果
执行后端业务判断
```

## 插件负责

插件负责：

```text
接收 FineJob 同步的执行队列
等待原有 loading 完成
读取当前页面
使用原有页面信息匹配执行队列
匹配失败时请求 FineJob 打开页面
执行具体 BOSS 动作
执行完成后第一时间报告 FineJob
执行后进入原有 cooldown/loading
页面保留时重新匹配当前页面
```

插件不能直接打开 BOSS 页面。
插件不能直接关闭 BOSS 页面。

---

# 四、Codex 必须确认前置 loading 的真实顺序

正确顺序必须是：

```text
插件请求 FineJob 打开页面
→ FineJob 实际打开页面
→ 原前置 loading
→ loading 完成
→ 插件读取页面/probe
→ 插件匹配执行队列
→ 匹配成功
→ FineJob lock/claim
```

Codex 必须找到当前工作区中真实负责以下行为的文件、函数和调用关系：

```text
requestTaskPage 或当前等价请求
FineJob 实际 open page
前置 loading 开始
前置 loading 完成
页面 probe
页面 URL / identity 读取
页面与任务匹配
任务 lock/claim
```

Codex 必须明确回答：

1. 当前工作区是否仍然保证 loading 完成后才开始页面匹配。
2. 当前工作区是否存在 loading 尚未完成就 probe/match 的路径。
3. 当前工作区是否存在先 claim/lock 再打开页面的路径。
4. 当前工作区是否存在先 claim/lock 再等待 loading 的路径。
5. 当前工作区是否新增了绕过原 loading 的统一执行路径。

Codex 必须根据调用链回答，不能根据变量名推断。

---

# 五、页面匹配必须继续使用原有能力

本次 cutover 不应该重新发明第二套页面匹配系统。

Codex 必须确认当前工作区实际使用什么来匹配页面，例如：

```text
URL
pathname
encryptJobId
currentPageTaskId
已有 read-only probe 结果
已有 action 自身字段
```

Codex 必须检查以下新增概念是否真正改变了执行行为：

```text
target_page_kind
target_page_key
target_context_key
page affinity
account affinity
account scheduler
same-page score
```

判定规则：

- 字段只存在于数据库、日志、兼容结构中，不算 bug。
- 字段参与实际页面匹配并替换原 URL/任务标识逻辑，属于潜在回归。
- 字段让插件等待不存在的“具体 HR session 页面”，属于回归。
- 字段让任务按不存在的账号调度模型分流，属于回归。

---

# 六、聊天页能力边界

当前系统没有“打开某个具体 HR UI 会话”的既有能力。

Codex 必须检查当前工作区是否错误依赖：

```text
切换到 HR-A 会话页面
切换到 HR-B 会话页面
target_context_key 命中具体 HR UI
当前聊天页必须打开目标 HR 才允许执行
```

如果不存在这些依赖，Codex 输出：

```text
CHAT_SESSION_UI_DEPENDENCY_NOT_PRESENT
```

如果存在，Codex 必须给出文件、函数和调用链。

---

# 七、统一执行队列的目标

统一执行队列需要统一管理：

```text
greeting
chat_message
resume_send
```

FineJob 负责给出：

```text
当前可以执行的任务
+
这些任务的优先顺序
```

插件负责：

```text
拿当前真实页面
→ 匹配 FineJob 已排序的执行队列
```

如果当前页面能匹配多个任务：

```text
插件从匹配任务中选择 FineJob 排序最靠前的任务
```

如果当前页面不能匹配任何任务：

```text
插件请求 FineJob 打开新的目标页面
```

Codex 必须检查当前工作区是否符合这个关系。

---

# 八、同一 HR 会话中的真实外发顺序

这里的顺序指：

```text
chat_message
resume_send
```

这些真实外发动作之间的业务顺序。

例如：

```text
A：回复 HR
B：发送附件简历
C：再次回复 HR
```

如果任务产生顺序是：

```text
A → B → C
```

实际外发也必须保持：

```text
A → B → C
```

Codex 必须检查：

1. 当前顺序机制是否只约束 `chat_message`。
2. 当前顺序机制是否把 `resume_send` 也纳入同一 HR 会话的真实外发顺序。
3. 后一个动作是否可能越过前一个尚未完成的动作。
4. 前一个动作结果为 `unknown` 时，后一个同 HR 外发动作是否会等待。

这里的 `unknown` 指：

> 插件已经尝试执行外发动作，但系统无法确认 BOSS 侧是否真的完成。

`unknown` 不是指 AI 无法理解 HR 消息。

---

# 九、Preflight 的准确位置

Preflight 必须发生在：

```text
页面打开
→ 前置 loading 完成
→ 页面匹配成功
→ 任务 lock/claim
→ Preflight
→ 真正执行
```

Codex 必须检查当前工作区是否满足这个顺序。

如果当前工作区存在：

```text
先 claim
→ 再找页面
→ 再打开页面
```

Codex 必须指出具体文件、函数和调用链。

---

# 十、chat_message 执行前检查

假设 HR 原来发来消息 A。

FineJob 根据 A 生成：

```text
Reply-A
```

真正轮到 Reply-A 执行时，系统必须先获取这个 HR 的最新聊天变化。

```text
读取最新聊天 delta
→ 保存最新 raw message
→ 判断消息语义
→ 与生成 Reply-A 时的基线比较
→ 决定接下来怎么处理
```

必须明确支持以下情况。

## 情况 1：没有新消息

```text
A
→ Reply-A 继续发送
```

---

## 情况 2：HR 又发了 B，B 与 A 属于同一个问题或上下文

例如：

```text
A：你什么时候可以到岗？
B：最好下周一之前。
```

此时：

```text
Reply-A 不直接发送
→ 原 Reply-A 被替代
→ 根据 A + B 重新生成新的回复
```

如果原 Reply-A 是用户人工确认过的，而新回复文本发生变化：

```text
新回复必须重新等待用户确认
```

旧确认不能自动授权新文本。

---

## 情况 3：HR 又发了 B，B 是独立的新问题

例如：

```text
A：你什么时候可以到岗？
B：另外方便发一下简历吗？
```

用户要求的正确行为是：

```text
Reply-A 保留
→ Reply-A 暂时不要单独发送
→ 等待 B 产生自己的后续动作/回复
→ B 的后续动作/回复准备完成
→ A 和 B 对应的外发动作一起进入发送阶段
→ 按原业务顺序发送
```

例如：

```text
Reply-A
→ Reply-B 或 resume_send
```

必须先发送 A 对应动作，再发送 B 对应动作。

这里的“一起发送”不是把两段内容强行合并成一条消息。

这里的“一起发送”指：

```text
两个动作都准备好以后
→ 按业务顺序连续进入执行
```

如果 B 需要用户确认：

```text
A 也等待
→ B 完成确认
→ 两个动作再按顺序进入发送
```

Codex 必须检查当前工作区是否错误实现成：

```text
发现 B
→ 立即发送 Reply-A
→ 之后再慢慢生成 B
```

如果当前工作区这样实现，属于不符合用户要求。

---

## 情况 4：B 只是平台事件/广告/无需回复事件

例如：

```text
resume_sent
resume_read_receipt
resume_viewed
resume_received_confirmation
platform_ad
platform_event_unknown 且明确无需回复
```

如果：

```text
reply_required = false
```

则：

```text
更新消息语义、已读/回复状态和相关活动
→ 原 Reply-A 继续执行
```

系统不能为这些事件生成普通 AI 回复。

---

## 情况 5：系统无法可靠判断 B 是否影响 A

系统不得猜测后继续发送。

正确行为：

```text
停止自动 dispatch
→ waiting / needs_review / replan_required
```

---

# 十一、resume_send 执行前检查

真正发送附件简历前，系统必须确认：

```text
目标 HR 已经有真人回复
用户选定的 encryptResumeId 仍然有效
用户选定的 filename 仍然对应同一份简历
没有已经成功或结果 unknown 的重复简历发送
```

平台广告、系统事件、附件状态不能当成 HR 真人回复。

用户已经选择某份简历后：

```text
执行前允许重新验证这份简历是否仍存在
```

但系统不能：

```text
发现原简历失效
→ 偷偷换成另一份简历
```

正确规则：

```text
0 份简历 → 阻止发送
1 份简历 → 自动使用唯一一份
多份简历 → 用户明确选择
```

如果已经选定的简历失效：

```text
停止发送
```

如果同一 HR 上一次 resume_send 已经成功：

```text
不能重复发送
```

如果上一次 resume_send 已经尝试执行但结果为 `unknown`：

```text
不能自动再次发送
```

---

# 十二、greeting 执行前检查

greeting 必须在：

```text
FineJob 打开目标岗位页
→ 前置 loading 完成
→ 插件匹配到目标任务
→ lock/claim
```

之后，再进行执行前检查。

至少确认：

```text
当前仍然是目标岗位
encryptJobId 仍然匹配
岗位仍然允许执行
当前仍然没有沟通过
没有同岗位已经成功的重复 greeting
没有同岗位结果 unknown 的 greeting
```

原 sender 已经存在的最后安全检查必须继续保留，例如：

```text
登录状态
页面 identity
encryptJobId
securityId
contacted == false
```

新的 Preflight 是额外保护。

新的 Preflight 不能替代原 sender 安全检查。

---

# 十三、执行结果必须第一时间报告 FineJob

插件执行每一个真实 BOSS 动作之后，插件必须第一时间报告 FineJob。

Codex 必须分别找到：

```text
greeting result report
chat_message result report
resume_send result report
```

并确认：

```text
执行
→ 立即 report FineJob
→ FineJob 更新任务状态
→ 执行后 cooldown/loading
```

如果当前工作区变成：

```text
执行
→ cooldown
→ 再 report FineJob
```

属于回归。

---

# 十四、执行结果的含义

FineJob 必须区分：

```text
transport accepted
```

和：

```text
BOSS 业务成功
```

传输层接受不能直接等于成功。

至少需要保持：

```text
执行中
transport accepted
succeeded
failed
unknown
cancelled
stale/superseded
```

只有可信的真实 outbound observation / reconciliation 才能确认 `succeeded`。

`unknown` 不得自动业务重试。

---

# 十五、执行后 cooldown/loading

原有执行后 cooldown/loading 必须保留。

正确顺序：

```text
执行
→ 第一时间报告 FineJob
→ FineJob 更新任务状态/队列
→ 原执行后 cooldown/loading
→ 下一轮页面匹配
```

Codex 必须找到当前工作区真实控制 cooldown/loading 的代码，并回答：

1. 原 cooldown 是否仍存在。
2. cooldown 的触发位置是否仍正确。
3. 新统一任务调度是否绕过 cooldown。
4. 新任务到达时是否可能在 cooldown 未结束前执行。
5. 页面复用路径是否绕过 cooldown。

---

# 十六、页面关闭配置

页面关闭不能被新的调度逻辑固定成：

```text
永远关闭
```

也不能固定成：

```text
永远保留
```

Codex 必须检查当前工作区中原有类似：

```text
close_page_after_completion
```

的配置是否仍然真实生效。

Codex 必须明确回答：

1. 当前哪个函数读取这个配置。
2. 当前哪个函数决定是否请求关闭页面。
3. 插件是否只发出请求。
4. FineJob 是否仍然执行实际关闭动作。
5. cutover 是否覆盖了原来的配置行为。

---

# 十七、页面保留后的下一轮

如果任务完成后页面没有关闭：

```text
插件必须先完成执行后 cooldown/loading
```

然后：

```text
插件使用当前页面
→ 匹配 FineJob 最新执行队列
```

如果当前页面有匹配任务：

```text
继续执行当前页面可以执行的最高优先任务
```

如果当前页面一个任务都匹配不到：

```text
插件才请求 FineJob 打开新页面
```

Codex 必须确认当前工作区是否仍然存在这个循环。

---

# 十八、本轮禁止事项

Codex 本轮不得：

```text
修改代码
提交代码
reset
restore
checkout 覆盖工作区
clean
stash
回退 commit
重做 Executor
重做统一任务系统
重做 Preflight
重新分析 MQTT/Techwolf 协议
访问真实 BOSS
发送真实消息
发送真实简历
执行真实 greeting
新增账号调度模型
新增聊天 UI session 切换能力
新增第二套页面匹配体系
```

---

# 十九、Codex 必须输出的结果

Codex 必须先输出一个总状态：

```text
POST_CUTOVER_WORKTREE_INTACT
```

或者：

```text
POST_CUTOVER_WORKTREE_REGRESSION
```

或者：

```text
POST_CUTOVER_WORKTREE_UNCERTAIN
```

## 如果是 INTACT

Codex 必须逐项说明：

```text
原前置 loading：保留/位置
loading 后页面匹配：保留/位置
页面匹配后 lock：保留/位置
Preflight：真实位置
chat_message 最新消息分支：真实行为
resume_send Preflight：真实行为
greeting Preflight：真实行为
结果立即回报：保留/位置
执行后 cooldown/loading：保留/位置
close_page_after_completion：保留/位置
页面保留后重新匹配：保留/位置
FineJob 打开/关闭页面责任：保留/位置
```

然后单独列出当前真实 bug。

---

## 如果是 REGRESSION

每一个问题必须使用：

```text
问题：
HEAD 原行为：
当前工作区行为：
文件：
函数：
调用链：
影响：
最小修复方式：
是否需要从 HEAD 恢复旧代码：是/否
```

Codex 必须基于当前代码给证据。

Codex 不得只写：

```text
可能
建议
看起来
应该看看
```

---

## 如果是 UNCERTAIN

Codex 必须写清楚：

```text
无法确认的问题：
缺少什么代码/运行证据：
为什么静态代码不能确认：
下一步最小验证方式：
```

Codex 不得因为无法确认就直接修改代码。

---

# 二十、本轮结束后才决定怎么修

本轮审计完成后只允许二选一：

```text
A. 原执行链仍然存在，只是新代码连接错了
→ 写最小 PATCH 任务
```

或者：

```text
B. cutover 确实覆盖/删除了原执行链
→ 只从 HEAD 恢复被破坏的具体旧行为
→ 保留已经正确实现的统一任务、优先顺序和 Preflight
```

除非当前工作区已经大面积不可恢复，否则不得默认整仓回退。
