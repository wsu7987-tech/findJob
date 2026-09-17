# 任务 03：父子 pause/resume/cancel + failure/interruption + 语义化卡点状态机

> v0.2.2.4 封版：状态、控制来源、版本号和事件幂等以 `E_状态_子任务_事件与实时传输契约.md` 为唯一解释。不得以“等价 reason”省略持久化字段或 API 返回值。

## 已确认决策 / 本任务主要完成事项

- parent 控制 child；child 自身状态变化也要反馈 parent。
- child pause 的目标父状态固定为 `waiting_child_paused`，不把“子任务暂停”和“用户暂停整个父编排”混为一谈。
- parent pause 才是父 `paused`。
- child stop 不 cancel parent；parent 进入等待人工决定。
- recoverable interruption 与 hard failure 都不能自动越过 child。
- 删除前端 `resume → advance` 拼接；每个业务控制由一个后端入口完成合法转换。
- interrupted/browser/context/codex 等已有卡点恢复能力必须保留。
- 每次业务控制必须有唯一 `transition_id`，并持久化 `control_cause`、`waiting_reason`、`state_version`；
- parent/child 的控制转换必须由同一个后端事务完成，前端不得串多个 API 拼一致性；
- parent 取消/暂停触发的 child 回调不能反向覆盖 parent 的更高优先级结果。

## 必须实现的语义

### parent pause

- parent → paused；
- linked child 请求安全 pause；
- child 未到安全点时可 pausing，但 parent 已禁止推进后续 child。
- 该转换必须记录 `control_cause=parent_pause`；迟到的 child paused 回调不得改成 child 自主暂停语义。

### child pause

- child → pausing/paused；
- parent → `waiting_child_paused`；
- 驾驶舱显示等待/子任务已暂停，不等同于“整个父任务被用户 pause”。
- 必须记录 `control_cause=child_self_pause` 或等价明确来源；恢复时只能解除该来源。

### resume

- parent resume 只恢复由 parent pause 造成的 pause；
- child resume 只解除 `waiting_child_paused` /对应 child interruption；
- 不得顺便越过 context budget、browser login、manual decision、failed waiting 等 unrelated reason；
- 前端一次点击只调一个业务 endpoint，后端完成原子转换并返回最新 snapshot。

### parent cancel

- parent 执行 `cancel` 时，对 Smart Capture child 只发 `stop` 控制动作，Smart Capture 最终 lifecycle 写 `stopped`；
- parent `cancelled`；
- 已取得的数据/结果不删除。
- parent cancelled 写入成功后，迟到的 child stopped event 只能被幂等消费，不能把 parent 改回 waiting decision。

### child stop

- child lifecycle → `stopped`；Smart Capture 对外 child command 使用 `stop`，parent `cancel` 只作为父层业务动作并通过 `control_cause` 记录来源；
- parent → `child_cancelled_waiting_decision`；
- 岗位采集页只提示返回驾驶舱；
- 驾驶舱提供：跳过该 child 继续 / 结束父任务。

### recoverable interruption

例如 app restart、BOSS executor 丢失、browser temporary unavailable 等可恢复情况：

- child → interrupted/具体 waiting；
- parent → `waiting_child_interrupted`；
- 只能通过明确 retry/resume 恢复；
- 不自动完成、不自动继续后续 child。

### hard failure

- child → failed；
- parent → `child_failed_waiting_decision`；
- 本任务不擅自新增“失败自动跳过”产品策略；
- `failed` 是 hard terminal；同一 child 不提供 retry/recover capability。凡是仍可 retry/resume 的错误必须落 `interrupted`/明确非终态 waiting；**（v0.2.2.4 已确认）** parent 在 `child_failed_waiting_decision` 下提供与 `child_cancelled_waiting_decision` 相同的决策集合：跳过该 child 继续后续编排 / 结束父任务，二选一；skip 不改变该 child 的 `failed` 终态、不暴露 retry。

### 状态持久化和事件幂等（强制）

- API 必须返回 `control_state`，不能只返回现有 legacy `status`/`paused`；
- `waiting_child_paused`、`waiting_child_interrupted`、`child_cancelled_waiting_decision`、`child_failed_waiting_decision` 的存储映射必须通过 migration 和 API tests 固定；
- child terminal event 至少带 `event_id/transition_id/child_relation_id/child_type/child_ref/state_version`；`state_version` 是 child lifecycle 版本；
- parent 消费 linked child completion/failure/interruption 必须以 `(child_relation_id,event_id)` 幂等，并仅接受 `event.state_version > relation.child_state_version`；接受后原子更新 `child_state_version`，relation 自身 `state_version` 独立递增；
- 同一 child 的 completed、stopped、failed 终态只能有一个有效 outcome；
- app restart 后未消费事件可以恢复，已消费事件不能二次推进父任务。

## 自动测试（强制）

覆盖：

- parent pause/resume；
- child pause→parent waiting_child_paused；
- parent cancel；
- child stop→waiting decision→skip；
- interrupted→parent waiting→retry/resume；
- hard failed→parent waiting decision，不自动推进、不对同一 failed child 暴露 retry；skip 后继续下一 child、end 后父任务结束，两条路径都需测试且都不改变该 child 的 `failed` 终态；
- resume 不越过 unrelated waiting reason；
- 重复 pause/resume 幂等或明确 409；
- parent pause→child callback、parent cancel→child callback 的竞态；
- 重复 child-completed、迟到 child-stopped event 不覆盖 parent 终态；
- 前端 Store 不再 `resume()` 后自动 `advance()`；
- parent 不越层直接找 BOSS operation_ref 控制 batch（过渡 bridge 除外，必须唯一执行 authority）。

## 用户手测（强制）

linked：驾驶舱 pause → child 安全暂停；child resume → parent 从 child-paused waiting 恢复；child stop → parent waiting 而非 cancelled；app restart 后需要明确恢复而不是自动推进。

## 高级模型复核

**建议。** 只审状态转换、递归调用、waiting reason、resume 越权、failure 处理。
