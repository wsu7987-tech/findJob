# HANDOFF_Task03：父子控制、失败中断与卡点状态机

执行日期：2026-09-18

## A. 已完成

1. Workflow Run 持久化并返回 `control_state`、`waiting_reason`、`control_cause`、`state_version` 与 `transition_id`；Smart Capture 和 child relation 同步返回 `transition_id`。
2. parent pause、resume、cancel 与 linked Smart Capture 的状态写入在同一 SQLite 写事务内完成；采集器 pause/resume/stop 在提交后调用。
3. child pause、resume、stop、recoverable interruption 与 hard failure 通过 linked child event 投影 parent；事件持久化包含 relation identity、transition identity 和 child lifecycle `state_version`。
4. 新增 parent child decision API：`POST /fine-job/workflow-runs/{workflow_run_id}/children/{child_relation_id}/decision`，以及 `skip`、`end` 便捷路径。决策只接受与当前 parent decision state 匹配的 stopped/failed relation。
5. hard failed child 保持 `failed`，不能 retry；parent pause/cancel、迟到回调和 skip/end 都不会改写该 child outcome。
6. 前端 Store 删除 resume 后的 `advance()` 拼接；驾驶舱仅在 parent 正处于 child stop/failed decision state 时显示 skip/end。

## B. 实际状态转换映射

| 入口/事件 | Child lifecycle | Parent control_state | 关键保护 |
|---|---|---|---|
| parent pause | running/pausing → pausing/paused，`parent_pause`；pending 保持 pending | `paused` | 不覆盖既有 waiting reason；暂停后的 child 回调不改 parent 语义 |
| parent resume | 仅恢复 `parent_pause` 造成的 pausing/paused | `active`；如 child 已 interrupted/failed/stopped，则转对应 waiting/decision | 不越过 child terminal/interruption |
| child pause | running/pausing → pausing/paused，`child_self_pause` | `waiting_child_paused` | parent 非 active 时拒绝覆盖其 blocker |
| child resume | child_self_pause 或 recoverable interruption → running | `active` | parent paused 时拒绝；不解除 context/browser/manual 等无关 blocker |
| parent cancel | 非终态 child → stopped，`parent_cancel` | parent cancelled | terminal child 原样保留；迟到 child event 不改 parent |
| child stop | stopped，`child_user_stop` | `child_cancelled_waiting_decision` | parent 可 skip/end，child 保持 stopped |
| child interrupted | interrupted，`recovery` | `waiting_child_interrupted` | 需要明确 resume/retry |
| child hard failed | failed，`child_failure` | `child_failed_waiting_decision` | retry=false；parent 可 skip/end，child 保持 failed |

## C. 事件幂等与版本

- `fj_workflow_child_events` 以 `event_id` 主键持久化 linked child event，并以 `(child_relation_id, event_id)` 作为 parent 消费 identity。
- parent 只应用 `event.state_version > relation.child_state_version` 的事件；成功后在同一事务更新 `child_state_version` 并独立递增 relation `state_version`。
- relation 已有 `completed/stopped/failed` outcome 时，任何不同状态的迟到 event 都记录为 terminal conflict，不能覆盖 relation 或 parent。
- parent `cancelled`、`completed` 等终态和 `paused + parent_pause` 优先于迟到 stopped/failed/interrupted callback。

## D. 改动文件

- `backend/app/db.py`：Workflow 语义控制字段、child event 表与原地 migration。
- `backend/app/services/fine_job/smart_captures.py`：父子原子控制、terminal outcome 保护、child control 前置条件。
- `backend/app/services/fine_job/workflow_children.py`：事件持久化、单调版本消费、terminal/parent 优先级保护。
- `backend/app/services/fine_job/workflow_runs.py`：parent pause/resume/cancel 与 failed/stopped decision API。
- `backend/app/routers/fine_job/workflow_runs.py`、`backend/app/routers/fine_job/smart_captures.py`、对应 schemas：transition request 与 decision endpoints。
- `apps/desktop/src/renderer/stores/fineJobWorkflowRun.ts`、`services/api.ts`、`types.ts`、`pages/fine-job/TaskCockpitNew.vue`：单 endpoint resume 与受 parent state 限制的 child decision UI。
- `backend/tests/api/test_fine_job_workflow_runs_api.py`、`backend/tests/test_db.py`、`apps/desktop/src/renderer/stores/fineJobWorkflowRun.test.ts`：状态、幂等、竞态和前端 resume coverage。

## E. 测试

本窗口按用户指令未运行测试。已编写/更新的覆盖包括：

1. parent pause/resume，重复 pause 幂等且不追加 advance；
2. child pause/resume 与 `waiting_child_paused`；
3. child stop 的 skip/end 两条路径；
4. hard failed child 的 retry 拒绝、skip/end 两条路径，且 child 仍为 failed；
5. interrupted 后明确 resume；
6. parent pause 与迟到 failed callback 的竞态；
7. parent cancel 与迟到 child event 的竞态；
8. 重复/旧版本 child event 不覆盖终态 parent；
9. child event schema/migration 字段；
10. 前端 Store resume 只调用一个 endpoint，不再调用 advance。

建议后续在获批后执行：

```text
pytest -q backend/tests/api/test_fine_job_workflow_runs_api.py backend/tests/test_db.py
```

桌面端测试应按现有 Vitest 环境单独获批执行。

## F. Authority / Owner 状态

- Smart Capture lifecycle/config/execution owner：`fj_smart_captures`。
- parent orchestration relation/projection owner：`fj_workflow_children`。
- linked outcome event owner：`fj_workflow_child_events`。
- production execution authority：Task09 前仍为单一旧 Workflow Pipeline Engine；本任务没有启动第二套 Smart Capture Engine。
- 历史 Workflow Run 查询与旧兼容路径保留。

## G. 轮询核对

本任务没有新增定时轮询。Workflow Store 的 `polling` 旧命名继续实际建立 SSE 订阅；因此未修改 `任务01_轮询风险核对.md`。

## H. 遗留风险

1. 本窗口未运行测试，需在用户允许后验证数据库迁移、API 与桌面端测试。
2. 当前 Phase 1 只有一个 linked Smart Capture。未来出现后续 child 时，skip 已将 parent 标记为 `start_next_child`；Task09 的 `ChildAdapter.start` 负责实际启动下一 child，不能在此之前引入第二套 live execution authority。
3. parent waiting state 当前由 child event/事务投影维护；后续新增 child type 时必须复用 relation identity、child lifecycle version guard 和 terminal conflict 规则。
