# E. 状态、通用 Child、事件幂等与 Smart Capture 实时传输契约（v0.2.2.4）

> 本文件继承 v0.2.2.3 的全部硬契约（含 v0.2.2.2 起固定的 failed/retry、事件乱序版本、independent outcome 边界、snapshot `state_version` 递增和 API 路径语义），并由 v0.2.2.4 封死 **hard failure 后 skip/end 的二选一语义**：`child_failed_waiting_decision` 与 `child_cancelled_waiting_decision` 采用相同的父层决策集合，不再是"是否允许 skip 待定"。Task 02、03、05、06、08A、08B、09、10、12、13、15、16 必须引用本文件。实现可以调整内部命名，但不能省略以下语义、字段和验收。

## 1. 状态存储的唯一解释

### 1.1 Smart Capture

Smart Capture 的 lifecycle status 必须明确支持：

```text
pending
running
pausing
paused
waiting_next_batch
waiting_for_user
interrupted
completed
stopped
failed
```

其中：

- `pending` 创建成功后立即占用 current slot；
- `paused`、`waiting_for_user`、`interrupted` 都是非终态；
- `completed`、`stopped`、`failed` 是终态；
- **`failed` 是 hard failure 终态**：同一 Smart Capture 不允许从 `failed` retry/recover，且 `capabilities.retry=false`；任何仍可恢复、允许 retry/resume 的执行错误必须落 `interrupted` 或其他明确的非终态 waiting，而不是先写 `failed` 再复活；若未来产品要“失败后重跑”，必须创建新的业务执行/另行设计，不在本轮把同一 terminal child 改回非终态；
- **`cancelled` 不是 Smart Capture lifecycle status**；Smart Capture child command/capability 只使用 `stop`。parent 的 `cancel` 是父层业务动作，映射到 child `stop`；落库后的 child lifecycle 统一归一为 `stopped`，并用 `control_cause` 区分 `parent_cancel` / `child_user_stop` 等来源；
- `waiting_reason` 必须区分 browser、context_budget、manual_decision、codex、child_control 等原因；
- `control_cause` 至少区分 `user_pause`、`parent_pause`、`child_self_pause`、`recovery`、`parent_cancel`。

`waiting_for_user` 是 user/manual blocker 的 canonical lifecycle status；新写入和 API snapshot 必须使用它。`waiting_next_batch` 仅用于系统等待下一批/Prefetch 等非人工等待。若 legacy row 曾以 `waiting_next_batch + waiting_reason` 表达人工等待，只能在 migration/read adapter 中规范化为 `waiting_for_user`；不得继续产生这种 legacy 组合。两者都属于非终态。

### 1.2 Parent Workflow Run

为保护现有 Workflow 历史 API，允许保留现有 `fj_workflow_runs.status` 的 legacy 值，但必须新增或等价提供以下语义控制字段：

```text
control_state:
  active
  paused
  waiting_child_paused
  waiting_child_interrupted
  child_cancelled_waiting_decision
  child_failed_waiting_decision
  waiting_other

waiting_reason
control_cause
state_version
```

API 快照必须返回 `control_state`。前端控制按钮不能只根据旧的 `status` 或 `paused` 布尔值猜测。

推荐保持如下兼容关系：

| 产品语义 | legacy lifecycle | control_state |
|---|---|---|
| 正常执行 | `running` | `active` |
| 用户暂停父任务 | 可保持 `running` 或现有 paused 表示 | `paused` |
| 子任务主动暂停 | `waiting_for_user` | `waiting_child_paused` |
| 子任务可恢复中断 | `waiting_for_user` | `waiting_child_interrupted` |
| 子任务取消 | `waiting_for_user` | `child_cancelled_waiting_decision` |
| 子任务失败 | `waiting_for_user` 或既有 failed-waiting 表示 | `child_failed_waiting_decision` |

具体 legacy 映射必须写入 migration 和 API tests，不得只写在前端。

## 2. 通用 Child relation

父任务必须有持久化的通用 child relation。默认新增或重建为 `fj_workflow_children`；如果复用现有表，必须提供同等字段和约束，不能只存 JSON/payload。

至少包含：

```text
id
workflow_run_id
child_type
child_ref
sequence
status
control_state / waiting_reason
control_cause
capabilities_json
result_summary_json
started_at
completed_at
created_at
updated_at
state_version
child_state_version   # parent relation 已接受的最新 child lifecycle version；不是 relation 自己的版本
```

约束：

- `(workflow_run_id, child_type, child_ref)` 唯一；
- `sequence` 决定父层编排顺序；
- `child_ref` 对 Smart Capture 指向 `smart_capture_id`；
- parent 只从此 relation 读取 child status、capability 和 result summary；
- `state_version` 是 parent relation/projection 自身的版本；`child_state_version` 是该 relation 已接受的最新 child lifecycle version，两者不得复用；legacy relation 可从 0/null 起步，但第一次消费新 child event 后必须写入明确值；
- parent 不从 BOSS batch、Prefetch、Handoff 或 Analysis item 私有字段判断 child 是否完成；
- linked parent 与 child 的双向关系必须可通过数据库查询校验。

### 2.1 Authority 边界（强制）

- `fj_smart_captures` 是 Smart Capture lifecycle/config/execution 的业务权威；
- `fj_workflow_children` 是 parent orchestration relation/projection 的权威；parent 从这里读取 child 顺序、父层状态摘要、capabilities 与 result summary；
- parent **不得直接写** Smart Capture lifecycle；child lifecycle 改变后通过 child event/同事务 projection 更新 `fj_workflow_children`；凡 relation projection 已确认观察到某个 child lifecycle version（无论 event 消费还是同事务控制投影），都必须同步推进 `child_state_version`，不能倒退；
- `fj_workflow_children.status/control_state` 是父层投影，不得反过来驱动或覆盖 Smart Capture 自己的 `status`；
- `fj_smart_captures.workflow_run_id` 保留为 linked 导航/历史反规范化 link；通用 parent→child canonical relation 是 `fj_workflow_children`；
- linked 创建时 `fj_smart_captures.workflow_run_id` 与 `fj_workflow_children` 必须在同一 SQLite 写事务中保持一致。

## 3. 状态转换与控制来源

每个业务控制请求必须带有或在后端生成唯一 `transition_id`。状态转换必须在同一个数据库事务中完成，外部 BOSS/Codex side effect 仍然在事务提交后执行。

### 3.1 不可混淆的语义

| 来源 | Child 结果 | Parent 结果 |
|---|---|---|
| 用户暂停 parent | child 请求安全暂停 | `control_state=paused` |
| child 自己暂停 | child paused | `control_state=waiting_child_paused` |
| child 可恢复中断 | child interrupted | `control_state=waiting_child_interrupted` |
| 用户取消 parent | parent `cancel` → child `stop` | parent cancelled |
| 用户停止 child | child `stop` → `stopped` | `child_cancelled_waiting_decision` |
| child hard failure | child failed | `child_failed_waiting_decision` |

### 3.2 竞态优先级

- parent cancel 的事务结果优先于由该 cancel 触发的 child stopped 回调；
- parent pause 的 `control_cause=parent_pause` 不得被 child pause 回调覆盖成 `child_self_pause`；
- child resume 只能解除对应的 child pause/interruption cause，不得清除 context、browser、manual decision 等其他 blocker；
- skip child 只能在 `child_cancelled_waiting_decision` 或 `child_failed_waiting_decision` 下执行（v0.2.2.4：两者共享同一决策集合 `{跳过该 child 继续后续编排, 结束父任务}`）；
- 一个 child 只能产生一次有效 terminal outcome。

## 4. Child event 与幂等

**以下 parent orchestration child event 只适用于 linked Smart Capture（存在持久化 `child_relation_id`）。** independent Smart Capture 不创建隐藏 Workflow Run/child relation，因此 terminal/interruption 只持久化自身 lifecycle、`result_summary` 和 domain snapshot，**不得为了满足本节 event schema 伪造 `child_relation_id` 或 parent outcome event**。

Linked child 的 completion/stop/failure/interruption event 至少包含：

```text
event_id
transition_id
child_relation_id
child_type
child_ref
child_status
result_summary
occurred_at
state_version   # 事件发送方 child lifecycle 的版本；不是 parent relation 的版本
```

`child_relation_id + child_type + child_ref` 用于稳定路由到具体 parent relation；不得假设 `child_ref` 在不同 child type 或不同 parent 之间天然全局唯一。Parent 必须以 `(child_relation_id, event_id)` 作为 canonical 幂等键。推荐使用事务 outbox 或持久化 child event 表；如果使用直接事务消费，必须有对应唯一约束和重复事件测试。

消费时还必须做**单调 child version guard**：将 event `state_version` 与 relation 的 `child_state_version` 比较；只有 `event.state_version > child_state_version` 才允许应用到 parent projection。`<=` 的事件即使 `event_id` 不同，也必须视为已观察/迟到旧事件，不得覆盖 parent 当前状态。成功应用后在同一事务内把 `child_state_version = event.state_version`，再独立递增 relation 自己的 `state_version`；绝不把 child version 直接赋给 relation `state_version`。

必须证明：

- 重复 child-completed 不会推进两个 child；
- 重复 stopped/failed/interrupted 不会覆盖后续人工决定；
- “新 `event_id` + 旧/相同 child `state_version`” 的乱序事件不会覆盖较新 parent projection；
- parent cancel 后迟到的 child event 不会把 parent 改回 waiting；
- app restart 后未消费 event 可以恢复，但已消费 event 不会再次推进。

## 5. Smart Capture Snapshot / Polling

Smart Capture 必须提供不依赖 Workflow ID 的 snapshot 契约。**API 路径语义统一如下**：FineJob router 的业务路径固定为 `/fine-job/smart-captures`；应用全局 HTTP prefix 为 `/api` 时，对外完整路径是 `/api/fine-job/smart-captures/...`。前端已有 API client 若自动添加 `/api`，调用字符串只写 `/fine-job/...`，不得再拼出双 `/api`；任务书不再使用含糊的 `/smart-captures/...` shorthand。

```http
GET /api/fine-job/smart-captures/current
GET /api/fine-job/smart-captures/{smart_capture_id}
```

快照至少返回：

```text
smart_capture_id
source
workflow_run_id | null
status
stage
waiting_reason
control_cause
state_version        # integer，唯一版本字段
capabilities: {
  start: boolean,
  pause: boolean,
  resume: boolean,
  retry: boolean,
  stop: boolean
}
progress
result_summary
updated_at
```

Smart Capture 自身**不新增第二套 `control_state`**。其可操作性由 `status + stage + waiting_reason + control_cause + capabilities` 唯一决定；`control_state` 只用于 parent orchestration。

前端可以使用 polling，也可以使用以 `smart_capture_id` 为 scope 的 SSE；但必须满足：

- independent 不依赖 Workflow SSE；
- linked 的 parent 镜像和 Smart Capture 详情是两个 snapshot source；
- history Run 查询不会切换 Smart Capture polling/control source；
- `state_version` 必须在**任何会改变 Smart Capture snapshot 可观察结果的已提交原子变更**后单调递增，至少覆盖 `status/stage/waiting_reason/control_cause/capabilities/progress/result_summary`；同一事务可只递增一次，no-op 不递增；若 capability 是派生值，则其底层原因变更必须带来版本递增；
- 断线后按 `smart_capture_id + state_version` 恢复，不用 latest Workflow Run 猜 current；
- terminal snapshot 仍可读取，直到新的 current 替换它。

## 6. Engine Cutover 的旧入口清单

Task 09 后，以下入口不得继续作为 Smart Capture live Engine：

```text
backend/app/services/fine_job/workflow_runs.py
  _on_capture_task_updated
  _advance_after_capture_finished
  advance_deep_job_search

backend/app/services/fine_job/smart_captures.py
  resume_smart_capture 中的 resume + advance 组合

frontend fineJobWorkflowCodexController
App-level advanceFineJobWorkflowRun controller
```

可以保留旧函数用于历史读取、旧任务恢复或明确的 legacy adapter，但必须满足：

- 不启动新的 Smart Capture BOSS/JD/Analysis/Prefetch 单元；
- 不把找不到 `smart_capture_id` 当作回退 Workflow live execution 的理由；
- 同一 linked child 只有一个 production execution authority；
- 有测试或静态 guard 证明旧 callback 没有继续触发 live advance；
- **Cutover 生效前**必须已存在最小 completion/outcome 生产闭环：Smart Capture terminal 判定可以持久化 `result_summary`，产生带完整 relation identity 的 child outcome event，linked parent 能按 `(child_relation_id,event_id)` 幂等消费，并用 relation `child_state_version` 拒绝乱序旧 child version。Task 10 负责把完整 OFF/ON completion policy 与 completed OFF 后处理继续封严，但不能承担“Cutover 后才第一次让 outcome 可达 parent”的基础接线。

## 7. 完整 Smart Capture Execution Config

`fj_smart_captures` 或其专属配置表必须持有 linked/independent 共用的完整执行配置，至少覆盖：

```text
search/filter/keyword/city
candidate_target_count
delivery_target_enabled
JD/detail policy
analysis batch / Codex model / reasoning / handoff
recommend/review targets
context budget / analysis guidance
stop policy
```

Workflow parent 只保存 child config reference 或必要的校验快照，不得成为 independent ON 运行所需的隐藏配置 owner。

Task 02 创建 linked/independent Smart Capture 时就必须持久化这份完整配置。允许第一阶段使用 `execution_config_json` 作为原子落库载体；Task 05 迁移的是 Pipeline 数据 owner，不得把 ExecutionConfig 延后到 Task 05 才成为 Smart Capture owner。

## 8. OFF completed 后处理

OFF 达候选目标后：

```text
Smart Capture completed
→ parent consume child-completed once
→ parent may continue
→ later Manual Analysis is post-processing only
```

后处理 API 必须以 `smart_capture_id` 为主 identity，并显式保证：

- 不修改 Smart Capture lifecycle；
- 不修改已推进 parent 的 control_state；
- 不重复发送 child-completed；
- 不重新占用 current slot；
- 不重新启动自动 Prefetch/Engine；
- 可以写入新的 Analysis artifact 和 Codex result。

## 9. Pending / restart / capacity

- `pending` 一旦创建就占 current slot；
- pending 必须有明确的 `start`、`stop`、`retry/recover` 入口；Smart Capture capabilities 固定为 `{start,pause,resume,retry,stop}`，**不提供 `cancel` capability**。parent 的 `cancel` 是父层业务动作，向 child 映射为 `stop`。Task 09 Cutover 后 linked child 由通用 `ChildAdapter.start(child_type, child_ref)` 调用 `SmartCaptureService.start(smart_capture_id)`，independent 由 Smart Capture create-and-start 或 create→start API 启动；
- 不能用更新时间自动把 pending/current 猜成另一条记录；
- 外部启动失败时保留 pending/interrupted 快照，并提供合法恢复动作；
- custom capture capacity 的占用判断必须与 Smart Capture 使用同一后端规则，不能只依赖一个页面的进程内状态。


## 10. Reservation 跨 Smart Capture 默认策略

本轮保持现有 **active reservation 对 `job_id` 全局唯一** 的语义，不在封版任务中引入“同一岗位可被多个活动 Smart Capture 重复 reservation”的新产品策略。

- completed Smart Capture 的 Manual Analysis 与 current Smart Capture 争用同一岗位时，返回明确 conflict 或复用既有 artifact；
- 冲突不得把 completed Smart Capture 改回非终态；
- 冲突不得阻塞/回滚已经推进的 parent；
- 后续若要支持跨 Smart Capture 并行重复分析，另开产品任务。

## 11. Phase 1 延期项：Smart Capture 历史浏览 UI

后端必须保留 `GET /api/fine-job/smart-captures/{id}` 历史读取能力（前端 API client 可按既有全局 prefix 写 `/fine-job/smart-captures/{id}`），但本轮不新增页面 Smart Capture ID 路由参数，也不改变 `/fine-job/capture` 的 current 语义。历史列表/选择器后续单独设计。
