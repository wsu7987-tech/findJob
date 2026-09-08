# FineJob：最终 Sol 独立验收（全部修复后）

## 任务目的

这是最终独立验收。

当前工作区包含：

```text
cutover
+ chat relation 五分支
+ greeting legacy→unified bridge
+ resume Preflight snapshot
+ shared dispatch/cooldown gate
+ cooldown 后当前页复匹配
+ greeting 异常结果 unified identity
+ bridge reject 收口
+ canonical succeeded 单调性
+ resume_list 创建阶段可达链
+ pause/resume unified Action 收口
```

所有改动仍未提交。

Sol 本轮只审查和测试，不修改代码，不提交，不访问真实 BOSS。

---

# 一、Git 安全要求

先检查：

```text
git status
git diff HEAD
git diff --cached HEAD
未跟踪源文件和测试文件
```

禁止：

```text
git reset
git restore
git checkout 覆盖工作区
git clean
git stash
git commit
```

---

# 二、最终业务执行顺序

必须确认三种真实动作都满足：

```text
FineJob 同步最新有序 eligible queue
→ 插件先完成现有 loading（若刚开页）
→ probe / 页面匹配
→ 对明确 Action ID claim
→ Preflight
→ dispatch_started
→ sender
→ side effect
→ 第一时间 report FineJob
→ canonical result 更新
→ shared cooldown
→ cooldown 后先复匹配当前页
→ 当前页无匹配才 request FineJob 开新页
```

不得存在：

```text
页面匹配前 claim
绕过 loading
绕过 shared gate
结果未同步就开始下一 Action
cooldown 中启动真实外发
当前页可复用却直接开新页
插件直接打开/关闭页面
```

---

# 三、共享 dispatch/cooldown gate

必须确认：

```text
FineJobExecutorClient
+
BossChatCoordinator
```

使用同一个 Background 级 gate。

任一 greeting/chat/resume 处于：

```text
claim
preflight
dispatch
等待 sender result
result sync
cooldown
```

另一循环不得启动新的真实 Action。

特别检查：

```text
chat result 首次回写失败 → outbox
→ 补传成功后仍进入 shared cooldown

greeting result 成功
→ 即使 legacy queue 为空
→ 仍进入 shared cooldown
```

---

# 四、cooldown 后当前页复匹配

必须确认：

```text
任务结果同步完成
→ cooldown
→ completed currentPageTaskId 约束解除
→ 原 URL/probe/encryptJobId matcher 匹配最新队列
```

当前页匹配成功：

```text
不 requestTaskPage
```

当前页无匹配：

```text
等待现有 match window
→ 才 requestTaskPage
```

---

# 五、chat relation 五分支

确认真实链：

```text
raw ingest
→ semantic
→ relation service
→ reply planning
→ Preflight
```

必须验证：

```text
无新消息 → A 继续

相关 B
→ A superseded
→ 现有 reply generation 生成 A'
→ A' 覆盖 A+B
→ A' 保持正确 session_sequence
→ 已确认文本变化时重新确认

独立 B
→ A 保留 waiting
→ B 进入现有 planning
→ B unified Action ready/确认后
→ A barrier 解除
→ A 先执行
→ B 后执行

reply_required=false 平台事件
→ 不生成普通回复
→ A 继续

classification_uncertain
→ fail closed
→ A 不 dispatch
```

---

# 六、greeting bridge

确认原页面链仍保留：

```text
legacy queue
→ FineJob 开页
→ loading
→ probe
→ encryptJobId match
```

页面匹配以后才：

```text
source_table/source_id
→ 唯一 unified greeting
→ exact action_id claim
→ unified Preflight
→ unified dispatch_started
→ 原 sender
```

确认：

```text
mapping missing/ambiguous
→ fail closed

claim/preflight reject
→ legacy 不卡 running/leased

unified terminal/unknown
→ legacy 不自动重发
```

---

# 七、greeting execution identity

所有真实 greeting sender 结果，包括：

```text
network error
timeout
internal exception
unknown
```

都必须保留：

```text
unifiedActionId
unifiedExecutionEpoch
```

以及当前 completion 需要的 token/owner。

已映射 greeting 缺 unified identity：

```text
必须拒绝 legacy-only completion
```

---

# 八、canonical 状态单调性

重点验证竞态：

```text
remote outbound observation
→ succeeded

稍后 sender completion
→ accepted / unknown / failed
```

最终必须仍是：

```text
succeeded
```

还要确认：

```text
stale execution epoch completion
→ 不覆盖当前 Action

duplicate trusted outbound echo
→ succeeded 幂等
```

这条规则应覆盖：

```text
greeting
chat_message
resume_send
```

---

# 九、resume_send Preflight snapshot

确认顺序：

```text
claim
→ Background snapshot request
→ Main World listResumeAttachments
→ snapshot result
→ backend exact encryptResumeId + filename Preflight
→ passed
→ dispatch_started
→ sender 再次精确校验
→ side effect
```

确认：

```text
timeout/error/mismatch
→ 不 dispatch

原简历不存在
→ 不自动换简历
```

---

# 十、创建阶段 resume_list

这是上轮 Sol 发现的问题 2。

必须确认当前已经真实可达：

```text
桌面请求刷新附件
→ create resume_list helper
→ 插件 discover pending helper
→ exact-ID claim
→ 复用现有 ResumeSnapshot/listResumeAttachments
→ helper result 回 FineJob
→ 0/1/多份选择链
```

必须确认：

```text
resume_list 不是 unified business Action
不进入 action_scheduler.complete_action
不产生 accepted/unknown/succeeded
不触发真实发送 cooldown
```

选择规则：

```text
0 份 → 阻止 resume_send
1 份 → 唯一自动选择
多份 → 用户明确选择
```

不得恢复“无 action_id 后端自行换领”。

---

# 十一、pause/resume unified Action 收口

这是上轮 Sol 发现的问题 4。

必须确认：

```text
session pause / manual takeover
→ 旧 fj_chat_send_actions 收口
+
fj_actions 中尚未真实 dispatch 的 chat_message/resume_send 收口
```

暂停前旧 Action 恢复 session 后不得重新 eligible。

重点检查：

```text
awaiting_confirmation
queued
waiting
claimed
preflighting
```

是否按无 side-effect 前提安全取消/终止。

不得粗暴取消：

```text
dispatching
accepted
unknown
succeeded
```

必须避免 pause 与 claim/preflight 的竞态。

恢复 session：

```text
不得自动复活 pause 前已取消 Action
```

---

# 十二、同 HR 顺序

确认：

```text
chat_message
resume_send
```

共享 session_sequence。

后继不得越过：

```text
claimed
preflighting
dispatching
accepted
unknown
waiting barrier
```

`unknown` 不自动业务重试。

---

# 十三、FineJob / 插件页面责任

确认：

```text
FineJob 实际打开页面
FineJob 实际关闭页面
插件只请求，不直接开关页面
```

`close_page_after_completion` 或现有等价配置继续有效。

不得出现新的：

```text
account scheduler
HR UI session 切换
page kind matcher
target_context matcher
```

---

# 十四、测试要求

请独立运行关键离线测试。

优先：

```text
backend:
- action scheduler
- action store Stage 1
- chat relation
- message relation
- greeting bridge
- boss executor
- resume Preflight snapshot
- BossChat API
- pause/resume/session status
- activity/outbound reconciliation

extension:
- FineJob client
- chat coordinator
- default greeting
- resume snapshot
- chat sender
- full vitest
- typecheck

desktop:
- resume selection related tests
- relevant full desktop suite if cost acceptable
```

禁止真实 BOSS。
禁止真实外部 AI provider。

---

# 十五、特别处理当前 Terra 全量测试结果

Terra 已报告：

```text
Extension:
80 passed

Desktop:
186 passed

Backend:
532 passed
16 failed
24 error
```

Sol 不得机械地因为 backend 非全绿直接判失败。

必须逐项判断 16 failed / 24 error：

## A. 旧 claim API 测试

已知有旧测试仍使用：

```text
claim without action_id
```

而当前业务要求：

```text
exact-ID claim
```

Sol 必须判断：

```text
这是测试契约过时
还是当前兼容 API 真实被破坏
```

不得为了旧测试恢复“后端自动换领”。

## B. resume_list 旧 accepted 断言

如果旧测试要求：

```text
resume_list 成功 → accepted
```

Sol 必须判断该断言是否已经与当前正确设计冲突。

当前设计：

```text
resume_list = read-only helper
```

不应获得真实发送 accepted 语义。

## C. pause 后保留旧 Action

如果旧测试要求：

```text
pause/manual takeover 后继续保留旧 unified Action
```

Sol 必须按当前业务要求判断测试是否过时。

当前要求：

```text
尚未 dispatch 的旧 Action 应收口，恢复后不得自动复活
```

## D. pytest temp 权限错误

Terra 报告 24 error 来自临时目录权限。

Sol 必须尽量：

```text
使用明确可写的工作区 temp 路径
```

复跑受影响测试。

如果仍是纯环境权限错误：

```text
单独列为 TEST_ENVIRONMENT_ERROR
```

不要当业务失败。

## E. 其他 10 个跨模块失败

公司治理、摄取、Codex JSONL、PDF、检索、Web Draft 等失败：

```text
必须判断是否由当前 worktree diff 引入
```

如果无调用关系且 HEAD/环境同样失败：

```text
列为 unrelated / pre-existing
```

如果当前 diff 导致：

```text
必须判 FIX_REQUIRED
```

---

# 十六、最终输出

第一行必须是：

```text
POST_CUTOVER_FINAL_PASS
```

或：

```text
POST_CUTOVER_FINAL_FIX_REQUIRED
```

或：

```text
POST_CUTOVER_FINAL_UNCERTAIN
```

然后完整输出：

```text
1. 总执行顺序
2. shared gate / cooldown
3. 当前页复匹配
4. chat relation
5. greeting bridge
6. greeting exception identity
7. canonical 状态单调性
8. resume_send snapshot
9. resume_list 创建阶段
10. pause/resume unified Action
11. 同 HR 顺序
12. 页面责任
13. 测试结果
14. 16 failed 分类
15. 24 error 分类
16. 仍需真实环境验证
```

若发现真实问题，每项必须写：

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

# 十七、PASS 标准

只有以下全部满足才能：

```text
POST_CUTOVER_FINAL_PASS
```

```text
三类真实 Action 共用正确执行/cooldown gate
页面匹配后才 claim
loading 不被绕过
result sync 后才 cooldown
cooldown 后先复用当前页
chat 五分支完整
独立 B 等 ready 后 A→B
greeting unified bridge + exception identity 完整
bridge reject 不残留 running
trusted succeeded 不被 completion 降级
resume_send snapshot 在 dispatch 前 Preflight
resume_list helper 创建阶段真实可达
resume_list 不进入业务发送 completion
pause 后旧未 dispatch unified Action 不会复活
unknown 不自动 retry
FineJob 负责真实页面开关
无重复 dispatch 路径
与本次改动相关的关键离线测试通过
```

真实平台的：

```text
greeting contacted reconciliation
MQTT outbound echo 时序
resume 最终平台确认
```

如果仅离线无法确认，应列为：

```text
仍需真实环境验证
```

不应单独因此判失败。
