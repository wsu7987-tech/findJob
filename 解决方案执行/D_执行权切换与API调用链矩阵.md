# D. Smart Capture 执行权切换 / API / Codex 调用链矩阵（v0.2.2.4）

> v0.2.2.4 封版：Task 09 的 Cutover 不仅切换入口，还必须证明旧 capture-finished callback、resume+advance 和 App-level Workflow controller 不再触发 live Engine。状态/事件/snapshot 细则见 `E_状态_子任务_事件与实时传输契约.md`。

> 目的：防止“DB owner 已迁，但 API/renderer/MCP 仍靠 Workflow Run”以及“旧新 Engine 同时执行”。

| 能力 | 当前主要入口/identity | 最终入口/identity | Cutover 前 | Task 09 Cutover 后 |
|---|---|---|---|---|
| Parent create | Workflow create | orchestration create + linked child | parent/child identity 原子建；旧执行 authority 唯一 | parent 只编排 child |
| Current capture | latest Workflow/伪 Smart | `/api/fine-job/smart-captures/current` | Task 13 前 UI 可未切 | 唯一业务 current |
| Search/BOSS | Workflow `advance` 直接启动 | Smart Capture Engine | 新 service 可测试，禁止双 live | parent 不再直接启动 |
| Candidate/JD | workflow_run_id | smart_capture_id | repository/seam 双读兼容 | Smart owner |
| Analysis Item/List/Save | `/workflow-runs/{id}/analysis-*` | Smart Capture Domain API | legacy API 保留；新 API 测通 | live 使用 Smart API |
| Manual Analysis Batch | Workflow endpoint | Smart Capture Domain API | 兼容存在 | live Smart API；completed 不回滚 |
| Current Context | workflow context snapshot | Smart Capture context by channel | legacy history 保留 | live Smart context |
| Historical Context | workflow_run_id | 保留 Workflow historical read | 始终保留 | 只读 inspected |
| Handoff claim/prompt/ACK/release | workflow_run_id | smart_capture_id + batch + attempt | Task 08A/08B 测通 | live Smart identity |
| Renderer handoff | `workflowCodexHandoff.ts` + FineJobWorkflowRun | owner-neutral/Smart Capture handoff | 不双触发 | Smart Capture live |
| Auto Codex controller | latest Workflow + `advanceWorkflow` | current Smart Capture Domain controller | guard 防双跑 | 不再调 Workflow advance 推 Prefetch |
| MCP FineJob tools | `*_workflow_*` + workflow_run_id | Smart Capture analysis tools | 旧 tool 保留兼容 | 新 prompt/tool 运行 Smart Capture |
| `.agents/skills/finejob` deep search | Workflow Run | Smart Capture domain run | Task 08A/08B 更新 | independent 不需 workflow id |
| CodexWorkspace/Terminal | workflow_run_id route query | Smart Capture/analysis identity；parent 可选 | 兼容旧历史 | 当前业务不靠 workflow id |
| Prefetch | Workflow `advance`/run | Smart Capture Engine | characterization | Smart owner 并行 |
| Reservation | workflow_run_id unique | smart_capture_id active unique | migration/repo 兼容 | Smart owner |
| Completion | Workflow 内部计数/advance | Smart Capture completion event | **Task 09 Cutover 前按既定 policy 打通最小 linked outcome，不重新定义最终规则；Task 10 再封严完整 policy** | parent 只消费 linked child outcome |
| Child relation/event | 无统一通用关系/回调 | 持久化 child relation + 幂等 event | Task 02/03 建立 | parent 只消费一次 outcome |
| Child start | 旧 Workflow advance/隐式启动 | `ChildAdapter.start` → `SmartCaptureService.start` | 契约/adapter 可测试，不启第二套 production | **唯一 live start 路径** |
| Live snapshot | Workflow snapshot/SSE | Smart Capture snapshot/polling/state_version | Task 08A/08B/13 测通 | independent 不需 Workflow id |
| Parent pause/resume/cancel | Workflow | Workflow orchestration service | 可 bridge 到唯一 child authority | 后端统一同步 child |
| History Run lookup | Workflow API | Workflow API | 保留 | 保留只读 |

## Authority 阶段

### Phase 0：当前
Workflow Pipeline 是事实执行主体，Smart Capture 半迁移。

### Phase 1：Task 02～04
Smart Capture 成为 child identity；建立 seam/characterization，但**生产仍只有旧一个 authority**。

### Phase 2：Task 05～08B
Schema/repository/domain API/transport 支持 Smart Capture；新路径可测试，仍禁止对同一 live child 双启动。

### Phase 3：Task 09
满足 Cutover checklist 后，一次性把 production child execution authority 切给 Smart Capture；同时启用通用 `ChildAdapter.start(child_type, child_ref)` 作为 linked child pending→running 的唯一父级启动入口。

### Phase 4：Task 10～15
完成语义、UI current/ownership、历史兼容和旧 live execution 路径清理。

## Cutover 不变量

- 任意时刻同一 child 只能有一个执行 authority；
- Cutover 后不允许“找不到 Smart owner 时自动回退 Workflow live execution”；
- legacy fallback 只能用于历史读取，不得重新执行；
- parent 不知道/控制 BOSS/JD/Analysis/Prefetch 内部单元。

## Task00 当前源码核对备注（2026-09-17）

| D 能力 | 当前源码核对 | 当前阶段结论 |
|---|---|---|
| Parent create | `workflow_runs.py:create_deep_job_search_run` 创建 Workflow，并在后续推进中直接产生 capture task；当前没有通用 `fj_workflow_children` 创建链。 | Phase 0，Workflow 是父级和执行入口。 |
| Current capture | `main.py` 未注册 `smart_captures.router`；`BossCapture.vue:658-660` 仍调用 `workflowStore.restoreLatest(false)`。 | Smart Capture current API 已有 service/router/client 文本，但应用 current 尚未切换。 |
| Search/BOSS | `workflow_runs.py:309-325` 在 `advance_deep_job_search` 内直接调用 `boss_capture_task_manager.start_capture(capture_source="smart")`。 | Workflow 仍直接启动 BOSS capture；Task09 前保持这一唯一 production authority。 |
| Candidate/JD | `fj_boss_capture_batches.smart_capture_id` 可写；Workflow task/payload 与历史岗位 API 仍是主要读取和推进入口。 | Smart Capture owner 仍未完成。 |
| Analysis Item/List/Save | `workflow_runs.py` 与 `routers/fine_job/workflow_runs.py:125-176` 提供 `/workflow-runs/{id}/analysis-*`；`api.ts:1622-1645` 直接调用这些路径。 | 新 Smart Capture Domain API 尚未形成。 |
| Manual Analysis Batch | `workflow_runs.py:create_manual_analysis_batch`；`BossCapture.vue:1124-1134` 使用 `createFineJobWorkflowManualAnalysisBatch`。 | 能力存在，当前以 Workflow ID 为主，completed Smart Capture 后处理语义尚未隔离。 |
| Current Context | `workflow_runs.py:get_context_snapshot` 读取 `fj_workflow_context_snapshots`；页面 `loadSmartContextSnapshot` 传 Workflow ID。 | 当前 live context 仍是 Workflow owner。 |
| Historical Context | `GET /api/fine-job/workflow-runs/{id}/context-snapshot` 与 `BossCapture.vue` Context channel 读取路径存在。 | 历史 API 保留，但 current/history 三源隔离尚未完成。 |
| Handoff claim/prompt/ACK/release | `workflow_runs.py:761-960`；`workflowCodexHandoff.ts` 的 `buildWorkflowPrompt` 携带 `workflow_run_id`；`api.ts:1585-1620` 使用 Workflow endpoint。 | claim/ACK/retry/release 已保留，尚未切 Smart Capture identity。 |
| Renderer handoff | `workflowCodexHandoff.ts` 的 `WorkflowCodexHandoffResult` 与 `FineJobWorkflowRun` 绑定。 | 新运行链仍依赖 Workflow Run。 |
| Auto Codex controller | `fineJobWorkflowCodexController.ts:18-41` 在 handoff/Prefetch 条件下直接调用 `api.advanceFineJobWorkflowRun`。 | App-level Workflow controller 仍推动 Prefetch。 |
| MCP FineJob tools | `fine_job_server.py:159-214` 的 get context/run/list/save/ACK 工具参数使用 `workflow_run_id`。 | legacy workflow tools 可用，Smart Capture tools 尚未接线。 |
| `.agents/skills/finejob` deep search | `SKILL.md:85-106` 要求读取 Workflow Run、Workflow Context、Workflow Analysis Items 并保存到 Workflow。 | Skill prompt identity 仍为 Workflow Run。 |
| CodexWorkspace/Terminal | `CodexWorkspace.vue:225-268` 和 `CodexTerminalPanel.vue:54-59` 从 route query 读取/回传 `workflow_run_id`。 | 当前任务定位仍依赖 Workflow route。 |
| Prefetch | `workflow_runs.py` `_advance_prefetch`/`_get_prefetch_summary`，前端 controller 调 Workflow advance；现有 workflow API 测试覆盖 ready/failed/abandon。 | 并行能力保留，owner 与执行入口待 Task09 后迁移。 |
| Reservation | Workflow prefetch/reservation 逻辑与两个 reservation 测试存在。 | 原子与释放 characterization 已固定，仍是 Workflow/Pipeline owner。 |
| Completion | `smart_captures.py:477-489` 只有 linked capture completed 标记；没有 child relation/outcome event 消费链。 | 最小 completion/outcome 闭环尚未建立，Task09 gate 不能提前满足。 |
| Child relation/event | 全仓源码核对未发现 `fj_workflow_children`、`child_relation_id`、`child_state_version` 的持久化实现。 | 当前没有统一持久化 relation/event authority。 |
| Child start | 当前由 `workflow_runs.advance_deep_job_search` 直接调用 `start_capture`；没有 `ChildAdapter.start`。 | Task09 前保持单一旧 authority，后续不可双跑。 |
| Live snapshot | Smart Capture service `get_smart_capture` 返回基础字段；`smart_capture.py` 未提供 `state_version/capabilities` 契约和独立 SSE/polling 接线。 | snapshot/polling 仍为半成品。 |
| Parent pause/resume/cancel | `workflow_runs` 提供 parent endpoints；`smart_captures.pause/resume/stop` 通过 `sync_workflow` 调旧 Workflow pause/resume/advance/cancel。 | parent/child 控制语义仍是旧 bridge characterization。 |
| History Run lookup | Workflow router `GET /{workflow_run_id}`、latest API 和前端 `smartRunLookupId` 存在。 | 历史读取能力保留，不能在后续迁移中删除。 |

### Task00 固定的 custom collection 入口

- 列表采集：`POST /api/fine-job/boss-capture/capture` → `boss_capture_task_manager.start_capture(capture_source="custom")`。
- MCP 列表采集：`finejob.start_job_capture` → `CodexToolService.start_job_capture` → 同一 custom `start_capture`。
- 列表续采：`POST /api/fine-job/boss-capture/tasks/{task_id}/continue`、`finejob.continue_job_capture` → 同一 task 的 `continue_capture`。
- 选中岗位详情：`POST /api/fine-job/boss-capture/tasks/{task_id}/details`、`finejob.collect_job_details` → `start_details`。
- 历史岗位详情：`POST /api/fine-job/boss-capture/history/{history_job_id}/details`、`finejob.collect_job_detail` → `start_history_detail`。
- 对照的 Smart/Workflow 列表启动：`workflow_runs.py:309-325` 和 `smart_captures.py:564-600`，两者传 `capture_source="smart"`，不归入 custom，但必须纳入 Task01 容量互斥回归。
