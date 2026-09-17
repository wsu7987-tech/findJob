# B. 父 Workflow / Smart Capture 控制状态矩阵（v0.2.2.4）

> 具体字段和 legacy status 映射见 `E_状态_子任务_事件与实时传输契约.md`。后端是权威；前端按钮只反映 capability/state。

| 动作 / 事件 | Child 前置 | Parent 前置 | Child 结果 | Parent 结果 | UI / 规则 |
|---|---|---|---|---|---|
| parent pause | 非终态可暂停 | running/waiting child | pausing/paused/安全等待 | **paused** | 父用户主动暂停；禁止推进其他 child |
| child pause | running | running/waiting child | pausing/paused | **waiting_child_paused** | 不冒充 parent user pause |
| parent resume | child 因 parent pause 可恢复 | paused | running/对应合法 stage | running/waiting child | 单一后端调用，不追加 advance |
| child resume | paused/interrupted 可恢复 | waiting_child_paused / waiting_child_interrupted | running | running/waiting child | 只解除对应 cause |
| parent cancel | 非终态 | 非终态 | stopped | cancelled | 两边终态，结果保留 |
| child stop | 非终态 | 非终态 | stopped | child_cancelled_waiting_decision | 返回驾驶舱处理 |
| parent skip stopped child | child `stopped` | child_cancelled_waiting_decision | 不变 | 下一 child/完成 | 只在该 reason 合法 |
| parent end after child stopped | `stopped` | child_cancelled_waiting_decision | 不变 | cancelled | 父层决定 |
| child recoverable interrupted | running/pausing | waiting child | interrupted | waiting_child_interrupted | 明确 retry/resume，不自动推进 |
| child retry after interruption | interrupted | waiting_child_interrupted | running | running/waiting child | 不越过别的 blocker |
| child hard failed | 非终态 | waiting child | failed | child_failed_waiting_decision | 不自动 skip/completed/cancelled |
| parent handle failed child | failed | child_failed_waiting_decision | **仍 failed** | 跳过该 child 继续后续编排 / 结束父任务（二选一，与 `child_cancelled_waiting_decision` 相同决策集合，v0.2.2.4 已确认） | failed 为 terminal；同一 child 不 retry，不提供万能 advance；skip 只是父编排跳过该 child，不等于把 child 改回非终态 |
| app restart | 依赖进程执行 | 非终态 | interrupted/安全等待 | waiting + 明确 reason | 不幽灵 running |
| OFF candidate target | running | waiting child | completed | 消费 result 并继续 | 不 waiting analysis |
| ON candidate target | running | waiting child | 继续 Analysis/Prefetch | 继续等待 child | 不提前 completed |
| ON result target | running | waiting child | completed | 下一 child/完成 | 正常完成 |
| OFF completed 后 Manual Analysis | completed | parent 已可能继续 | **仍 completed** | **不变** | 后处理不回滚 lifecycle |

## Capability 规则

- `resume/retry` 只在具体**非终态可恢复** reason 下暴露；`failed` 必须 `retry=false`。
- `skip child` 是父编排能力，不放岗位采集页直接执行。
- 通用用户 `advance` 不存在；内部 transition 函数可保留。
- 前端不得串行调 child+parent API 拼一致性。
- **（v0.2.2.4 已确认）** hard failure 允许 skip：`child_failed_waiting_decision` 与 `child_cancelled_waiting_decision` 共享同一父层决策集合 `{跳过该 child 继续后续编排, 结束父任务}`；skip 不等于 retry，不改变该 child 的 `failed` 终态与 `retry=false`。
- 每一行转换必须记录 `transition_id`、`control_cause`、`waiting_reason`、`state_version`；
- parent cancel/pause 触发的迟到 child event 不得覆盖 parent 的更高优先级结果；
- linked child completion/failure/stop event 必须带 `child_relation_id/child_type/child_ref/event_id/state_version`，并按 `(child_relation_id,event_id)` 幂等消费；relation 另存 `child_state_version`，只接受更高 child version；independent 不产生 parent orchestration event。

## 最低测试组合

1. parent pause/resume；
2. child pause/resume；
3. parent cancel；
4. child stop → waiting → skip；
5. interrupted → waiting → retry；
6. hard failed → waiting，不误推进、同一 child 不 retry；
6a. hard failed → waiting → skip（跳过继续后续编排）与 → end（结束父任务）两条路径都可用，且 skip 后该 child 仍保持 `failed`；
7. 非法 skip/resume/retry 409/422；
8. restart；
9. OFF post-analysis 不回滚；
10. parent/child callback 竞态与重复/迟到 event；
11. `control_state` 与 legacy status/paused 字段的 API/migration 映射。
12. Smart Capture lifecycle 只使用 `completed/stopped/failed` 等既定终态，**不新增 `cancelled` status**；cancel/stop 的来源写入 `control_cause`。
