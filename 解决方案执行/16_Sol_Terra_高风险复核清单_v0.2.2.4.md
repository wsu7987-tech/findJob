# 16. Sol / Terra 高风险只读复核清单 v0.2.2.4

> v0.2.2.4 封版硬契约统一见 `E_状态_子任务_事件与实时传输契约.md`。复核重点是实现是否遵守契约，不重新设计产品。

> 高级模型主要做代码/架构 review，不负责大量机械测试。实现和测试优先 Luna。

## 节点 A：Task 03 后 —— 父子状态机

检查：

- parent↔child sync 是否递归；
- child pause 是否落 `waiting_child_paused`，而不是与 parent user pause 混淆；
- child stop 是否仍 cancel parent；
- interrupted/failed 是否会误推进；failed terminal 是否错误暴露 retry/recover；
- **（v0.2.2.4）** `child_failed_waiting_decision` 是否提供与 `child_cancelled_waiting_decision` 相同的“跳过该 child 继续 / 结束父任务”二选一，skip 后该 child 是否仍保持 `failed` 且不暴露 retry；
- resume 是否越过 unrelated waiting reason；
- 前端是否仍 `resume→advance`；
- skip child 是否只在正确 waiting reason；
- 创建事务是否可能留下 parent/child 孤儿。
- `control_state/waiting_reason/control_cause/state_version` 是否有明确持久化和 API 映射；
- parent pause/cancel 与 child 回调竞态是否有测试；
- linked child event 是否带 `child_relation_id/child_type/child_ref/event_id/state_version`，并按 `(child_relation_id,event_id)` 幂等消费；relation 是否持久化 `child_state_version` 并拒绝新 event_id + 旧版本；independent 是否错误创建 parent event/relation。

## 节点 B：Task 06 后 —— Schema/Repository owner

检查：

- migration 是否原地升级；
- independent `workflow_run_id=null` 是否可存全部 Pipeline；
- unique/index 是否仍正确；
- legacy 无法回填的数据是否被伪造；
- 新 repository 是否真正以 Smart Capture 为权威；
- Pipeline 每张表是否完成 owner/FK/unique/index 迁移，而不是只增加一列；
- independent discovery/JD/Analysis/feedback 是否摆脱 Workflow-only FK；
- 是否偷偷用 workflow fallback 决定 new business state；
- production authority 是否仍保持唯一、尚未提前双跑。

## 节点 C：Task 09/10 后 —— 最高风险

检查：

- Smart Capture 是否真正是 production Pipeline owner；
- parent 是否仍直接启动 BOSS/JD/Analysis/Prefetch；
- independent 是否偷建隐藏 Workflow Run；
- Codex renderer/MCP/Skill 是否仍要求 workflow_run_id；
- Analysis N + Prefetch N+1 是否并行；
- reservation/handoff race/unique 是否正确；
- Cutover 是否可能让同一 child 双启动；
- pause/restart 是否产生幽灵执行；
- snapshot/polling 是否在 independent 无 Workflow ID 时可用；
- 旧 capture-finished callback、resume+advance、App-level Workflow controller 是否仍能触发 live Engine；
- completed OFF 后处理是否使用 Smart Capture identity 且不修改 lifecycle；
- OFF 达候选目标是否 completed；
- OFF completed 后 Manual Analysis 是否回滚 lifecycle/parent；
- ON 是否没有提前 completed；
- linked completion event 是否按 relation identity + child version guard 消费；Task 09 Cutover 前是否已存在最小 completion/outcome 闭环，而不是到 Task 10 才首次接通；independent 是否保持无 parent event。

## 节点 D：Task 13/14 后 —— BossCapture 高风险前端

检查：

- current Smart Capture 是否唯一来自 current API；
- linked parent A 与 inspected history B 是否完全隔离；
- 输入历史 B 是否污染 control/SSE/Codex polling；
- independent 是否误恢复 latest parent；
- Workflow 区是否通过“删掉”而不是“迁归属”整理；
- Context ID/下拉、Codex、Planner、Prefetch、Manual Analysis 是否仍存在；
- parent 镜像显隐是否只隐藏镜像；
- 通用立即推进删除后合法卡点 action 是否仍在。
- 状态数据库 CHECK、TypeScript/Pydantic schema、API snapshot 和前端 capability 是否一致；
- current/history/control/Codex 的 polling source 是否完全隔离；

## 输出要求

只输出：

1. Blocking issues；
2. High-risk non-blocking issues；
3. 已确认安全项；
4. 建议交回 Luna 的最小修复清单。

不要重新设计产品，不大范围改代码，不负责机械测试。

## v0.2.2.4 封版专项复核

- Smart Capture 是否错误新增了 `cancelled` lifecycle 或 `cancel` capability（应使用 `stopped` + `stop`；parent `cancel` 只映射为 child `stop`）；
- `fj_smart_captures` 与 `fj_workflow_children` 是否形成双 lifecycle source；
- Task 02 是否已让完整 ExecutionConfig 归 Smart Capture，而不是等到 Task 05；
- Task 08A/08B 是否都显式遵守 E 契约，08B 是否避免重新实现 08A 后端业务逻辑；
- Task 09 前置是否真的覆盖到 08B，Cutover 前是否已有最小 completion/outcome 闭环；
- Task 09 后 linked `pending` child 是否只通过 `ChildAdapter.start → SmartCaptureService.start` 启动；
- linked child outcome event 是否包含 `child_relation_id/child_type/child_ref/event_id/state_version`，并以 `(child_relation_id,event_id)` 幂等；independent 是否不产生 parent outcome event；
- event `state_version` 是否明确是 child lifecycle version，relation 是否另存 `child_state_version` 并只接受更高版本，parent relation `state_version` 是否独立递增；
- Smart Capture snapshot 是否只使用整数 `state_version`，是否不存在第二套 `revision`；任何 snapshot 可观察变化是否都使版本单调递增；
- `waiting_for_user` 是否为人工阻塞 canonical lifecycle，新写入是否仍偷用 `waiting_next_batch + reason`；
- active reservation 是否保持本轮 `job_id` 全局唯一，冲突不回滚 completed parent/capture。

- `failed` 是否保持 hard terminal 且 `retry=false`；所有可恢复错误是否落 `interrupted`/明确非终态。
- Parent mirror 是否使用 `cancel`（后端映射 child `stop`），而不是把 parent action 写成 `stop`。
- Smart Capture API 是否统一 FineJob router `/fine-job/smart-captures`（完整 HTTP `/api/fine-job/...`），没有 `/smart-captures/...` shorthand。
