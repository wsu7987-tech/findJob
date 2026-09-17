# 任务 09：Prefetch / Reservation / Handoff 并行保真 + Smart Capture Engine 正式 Cutover

> v0.2.2.4 封版：本任务必须按 `E_状态_子任务_事件与实时传输契约.md` 执行。Task 09 是唯一允许切换 production authority 的任务，但不是允许保留旧 live fallback 的任务。

## 本任务的重要性

这是 v0.2.2 最高风险任务，也是**唯一允许正式切 production execution authority** 的任务。

前置：Task 04～08B 的 characterization、schema、repository、Search/Candidate、08A Domain API/MCP、08B Renderer/Codex transport 全部通过。

## 已确认决策 / 本任务主要完成事项

1. 保留 Analysis N 与 Prefetch N+1 并行优化。
2. Candidate Reservation 防止同岗位进入多个活动 Analysis/Prefetch。
3. ready Prefetch 可 promotion 成下一正式 Analysis Batch。
4. Handoff started ACK 后可触发/推进下一批详情准备。
5. linked/independent 完全共用 Smart Capture Engine。
6. **在最小 completion/outcome 闭环通过后，正式将 production authority 从旧 Workflow Pipeline 切到 Smart Capture。**

## Cutover 前检查（必须先通过）

- characterization tests 全绿；
- independent Analysis/Codex transport 不需要 workflow id；
- repository 新 owner 全链路可用；
- custom collection capacity 互斥可用；
- parent/child 状态机可用；
- migration/legacy read 已验证；
- **最小 completion/outcome 闭环已验证**：linked Smart Capture 能按当前已确认 completion policy 进入 terminal、持久化 `result_summary`、发出带 `child_relation_id/child_type/child_ref/event_id/state_version` 的 outcome event；parent 按 `(child_relation_id,event_id)` 幂等，且仅接受高于 relation `child_state_version` 的 child version。independent terminal 只持久化自身结果，不创建 relation/outcome event。Task 10 可继续补齐完整 OFF/ON 边界和 OFF completed 后处理，但不能等到 Task 10 才第一次打通 linked outcome。

任一失败，不允许切换 authority。

## 并行保真要求

```text
Analysis Batch N → Codex analysing
        │
        └── Prefetch Batch N+1 → JD preparing

Codex N 完成：
- N+1 ready → promotion，直接形成下一 Analysis Batch
- 未 ready → 等待剩余详情，但不重复 reservation / JD
```

不得退化成“Codex N 完成后才开始抓 N+1 JD”的全串行。

## 正式 Cutover

Cutover 后：

- parent Workflow 不再直接调用 `boss_capture_task_manager.start_capture()` 作为 deep job search 执行主体；
- parent 不再直接创建/推进 JD/Analysis/Prefetch；
- `advance_deep_job_search()` 不再作为 Smart Capture live Engine 的通用推进器；
- App-level Codex controller 不再靠 `advanceFineJobWorkflowRun` 推 Prefetch；
- child/pipeline 的实时 snapshot 由 Smart Capture Domain 提供；
- Workflow 只等待/消费 child status/result；
- legacy Workflow API 只保留历史读取与必要兼容 adapter。

### 旧 live 入口清理（强制）

除非明确改成委托 Smart Capture Domain，否则以下入口必须禁用、拆除或仅保留历史读取：

```text
workflow_runs._on_capture_task_updated
workflow_runs._advance_after_capture_finished
advance_deep_job_search 作为 Smart Capture live Engine
smart_captures.resume_smart_capture 中的 resume + advance
fineJobWorkflowCodexController / App-level advanceFineJobWorkflowRun
任何 workflow_run_id 缺失时回退 Workflow live execution 的分支
```

Cutover 需要有静态搜索或运行时 guard 证明旧 callback 不会再次启动 linked child 的 BOSS/JD/Analysis/Prefetch。

### Snapshot / parent 消费边界

- Smart Capture Domain 负责 child/pipeline snapshot、唯一整数 `state_version` 和合法控制 capabilities `{start,pause,resume,retry,stop}`；不得新增 `revision` 或 `cancel` capability；
- Workflow 只消费通用 child status/result summary；
- parent 不读取 `operation_ref_id` 或内部 batch 字段决定 child 是否完成；
- history adapter 不得触发 live transition。

必须有测试证明**同一个 linked child 只会启动一套 BOSS/JD/Analysis/Prefetch**。

## pause/restart

- pause 后不再启动新 execution unit；
- 已在安全执行中的单元按能力收敛；
- restart 后丢失的 BOSS/Codex 本地进程不能显示为仍在 running；
- reservation/handoff orphan 有明确恢复/释放规则。

## 自动测试（强制）

- linked/independent 走同一 Engine；
- Analysis N + Prefetch N+1 并行；
- ready promotion；
- reservation 不重复；
- handoff claim race；
- pause/restart；
- production linked 只启动一次 BOSS batch；
- parent 不越层执行 child internal unit；
- 旧 capture-finished callback、resume+advance、App-level Workflow controller 不再触发 live Engine；
- Smart Capture snapshot/polling 在 independent 无 Workflow ID 时可用；
- linked child completion/failure event 重复或迟到（含新 event_id + 旧 state_version）时不二次推进/覆盖 parent；independent 不产生 parent event；
- old Workflow history queries 仍工作。

## 用户手测（强制）

用低目标 ON 任务：观察当前 Analysis/Codex 进行时下一批详情已开始准备；完成当前批后 ready next batch 可直接交接。linked 与 independent 各测一次即可。

## 高级模型复核

**强烈建议 Sol/Terra 只读复核。** 这是最高优先级 review 节点。

## Cutover 后 Child Start 契约（强制）

Cutover 生效后，linked parent 启动当前 child 的唯一合法路径是：

```text
Parent Orchestrator
→ ChildAdapter.start(child_type, child_ref)
→ SmartCaptureService.start(smart_capture_id)
→ pending → running
→ Smart Capture Engine
```

- Parent Orchestrator 不知道 BOSS/JD/Analysis/Prefetch 的具体 start；
- independent Smart Capture 必须由明确 create-and-start 或 create→start API 进入 running；
- 旧 Workflow `advance_deep_job_search` 不得承担 Smart Capture pending→running；
- start 失败保留 `pending`/`interrupted` snapshot，并暴露合法 `retry/stop` capability；parent `cancel` 映射为 child `stop`；
- 同一 `transition_id` / start request 必须幂等，不能双启动。
