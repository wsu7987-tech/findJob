# 任务 15：集成回归 + 旧兼容路径清理

> v0.2.2.4 封版：最终回归必须覆盖 `E_状态_子任务_事件与实时传输契约.md` 的状态、child relation、event idempotency、snapshot/polling 和 cutover guard。

## 目标

不新增产品能力。证明最终系统只有一套业务 owner、current identity、execution authority 和父子控制关系，并安全移除会重新制造混乱的旧运行路径。

## 必查旧路径

- BossCapture `restoreLatest(false)` 作为 current identity；
- Workflow Run 拼 `currentSmartCapture`；
- linked parent 与 inspected history 共用 store/control state；
- Workflow Run 仍直接启动 BOSS/JD/Analysis/Prefetch；
- `workflow_runs._on_capture_task_updated` / `_advance_after_capture_finished` 仍触发 live advance；
- `resume_smart_capture` 仍通过 resume + advance 组合推进旧 Workflow；
- App controller 仍 `advanceFineJobWorkflowRun` 推 Smart Capture Prefetch；
- 新 Codex prompt/MCP 仍要求 independent 提供 workflow id；
- `workflow_run_id` fallback 仍决定 new owner/completion；
- delivery OFF 仍 waiting analysis；
- completed OFF Manual Analysis 会回滚 capture/parent；
- independent 偷建隐藏 Workflow Run；
- Smart Capture 与 custom capture 互斥入口不一致；
- pending/paused/waiting 没占 current slot；
- child failed/interrupted 时 parent 自动推进，或 failed terminal 仍暴露同一 child retry；
- parent pause/cancel 回调竞态覆盖了更高优先级状态；
- child-completed / stopped / failed event 重复消费或迟到覆盖 parent；relation 未保存/比较 `child_state_version`；
- parent child relation 只在 JSON/前端内存中，没有持久化顺序和 result summary；
- independent Smart Capture 仍没有 snapshot/polling 或依赖 Workflow SSE；
- parent/Smart Capture 状态字符串与数据库 CHECK/type/API 不一致；
- Smart Capture 完整 Execution Config 仍只在 Workflow contract；
- 通用用户“立即推进”残留。

## 完整回归矩阵

### linked OFF
创建→current→candidate target→child completed→parent 下一 child/完成；无 Codex 自动阻塞；后处理 Manual Analysis 不回滚 parent。

### linked ON
Candidate→JD→Analysis/Codex + Prefetch→结果目标→child completed→parent 继续。

### independent OFF/ON
无隐藏 Workflow Run；ON 可完整 Codex/Prefetch；OFF 正常完成。

### Smart vs custom capacity
任何非终态 Smart Capture 与 custom start 互斥；custom running 与 Smart create/start 互斥；多入口一致。

### 控制
parent/child pause/resume；parent cancel；child stop→parent waiting→skip/end；child interrupted/failed→parent waiting，不误推进。

### restart
不幽灵 running；current stable；合法恢复；orphan reservation/handoff 不重复。

### 状态/事件/实时传输

- parent `control_state`、`waiting_reason`、`control_cause`、`state_version` 与数据库/API/前端一致；
- child relation 有持久化 sequence/status/result summary/`state_version`/`child_state_version`；
- linked 重复和迟到 child event 幂等，且新 event_id + 旧 child state_version 也被拒绝；independent 不产生 parent orchestration event；
- independent 无 Workflow ID 仍能 snapshot/polling、恢复和读取 terminal result，且不创建隐藏 relation/outcome event；
- current/history/control/Codex 的 polling source 完全隔离。

### history/current 隔离
linked A + history B；independent + history B；控制/Codex/SSE 都不被 B 污染。

### Pipeline
Analysis N + Prefetch N+1 保持并行；reservation 唯一；handoff claim/ACK/retry 正常。

### 页面保护
Context 下拉、历史 ID、Planner、Candidate/JD、Analysis、Codex、Prefetch、Manual Analysis、结果区等仍在正确 owner 下。

## 测试策略

- 不删旧 characterization tests；
- 能迁到 Smart Capture owner 的测试迁断言，不降语义；
- legacy Workflow history tests 保留；
- 前端关键状态隔离有组件/service tests。

## 用户最终手测建议

1. linked OFF；
2. linked ON；
3. independent OFF；
4. independent ON + Codex/Prefetch；
5. Smart paused 时尝试 custom；
6. custom running 时尝试 Smart；
7. parent/child pause/resume；
8. child stop → waiting → skip；
9. restart interrupted；
10. historical Run/Context；
11. side nav/cockpit 同 current；
12. OFF completed 后人工 Analysis 不回滚 parent；
13. 反复刷新、断线重连、创建响应丢失时不会产生第二个 current 或第二组 parent/child。

## 高级模型复核

只在前面高风险节点发现问题或最终 diff 很大时再做，不用高级模型跑机械测试。
