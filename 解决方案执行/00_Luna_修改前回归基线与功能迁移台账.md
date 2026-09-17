# 任务 00：修改前回归基线 + 功能迁移台账确认

## 已确认决策 / 本任务主要完成事项

本任务不做架构重构。目标是把**现在真实存在且必须保留的能力**固定成测试/台账，防止后续 Luna 在迁移时用“删功能”换取简单实现。

本封版包以 **v0.2.2.3 为直接文档基线**，v0.2.2.4 仅补充 hard failure 后的父层 skip/end 决策；Task 00 仍必须对当前 `task-cockpit-phase1` 源码重新核验并固定：

- Workflow Run 历史 ID / Context 查看；
- Smart Capture 半成品 API；
- 自定义采集与智能采集互斥入口；
- Workflow-based Codex Handoff / Analysis API / MCP tools / renderer controller；
- Analysis N + Prefetch N+1 并行行为；
- Manual Analysis；
- interrupted/browser/context-budget 等卡点恢复行为；
- BossCapture 页面主要功能区存在性。
- parent child relation、状态字段、completion event 幂等和 Smart Capture snapshot/polling 契约。

## 当前源码已知事实

- Smart Capture API/service 已有半成品，但未完整接线；
- Pipeline 仍主要挂在 Workflow Run；
- BossCapture 是高风险大页面，混合 Workflow Run、Context、Codex、Planner、Candidate/JD 等能力；
- Codex transport 与 MCP Skill 仍强依赖 `workflow_run_id`；
- 自定义采集入口仍存在，不能在 Smart Capture 重构中被互斥逻辑绕过。

## 允许修改

- 测试文件；
- `A/B/C/D/E` 台账的源码核对备注；
- 极小测试辅助 helper。

## 禁止事项

- 不改业务 schema；
- 不迁 Smart Capture/Workflow ownership；
- 不重构页面；
- 不通过降低旧测试断言得到“绿灯”。

## 实施要求

1. 运行并记录当前后端/前端相关测试命令与结果。
2. 对 `A_现有功能迁移台账.md` 逐项核对源码入口。
3. 对 `D_执行权切换与API调用链矩阵.md` 当前列逐项核对：
   - Workflow Analysis API；
   - Handoff claim/prompt-written/ack/release；
   - `workflowCodexHandoff.ts`；
   - `fineJobWorkflowCodexController.ts`；
   - CodexWorkspace / Terminal；
   - MCP FineJob workflow tools 与 `.agents/skills/finejob/SKILL.md`。
4. 记录自定义采集的全部开始入口，至少包括 BossCapture API 和 Codex tool 路径，作为 Task 01 互斥回归清单。
5. 确认 Workflow Run 历史 ID 可按 API 读取；Context 类型下拉/历史查看当前行为记录为 characterization，即使目前存在 bug 也不要“美化”基线。
6. 对 Prefetch/Reservation/Handoff 现有测试建立清单，后续迁移不得删断言。

## 自动测试

以项目现有测试为主；可以新增 characterization tests，但本任务不修业务行为。

## 用户手测

无需完整手测。要求输出：当前测试结果、失败基线、现有功能入口清单、后续最危险回归点。

## 高级模型复核

不需要。

## Task00 执行记录

执行日期：2026-09-17

执行分支：`task-cockpit-phase1`

执行前基线提交：`389c957ddad6a7af51f2d28d80f550285a688459`

本任务只做源码核验、测试基线和台账记录。没有修改业务 schema、业务 service、API 行为、前端页面或测试逻辑；已有工作区文件保持原样。

### A. 已完成

1. 已按 A 台账逐项核对现有能力入口，核对结果已追加到 `A_现有功能迁移台账.md`。
2. 已按 D 矩阵逐项核对当前 API、Workflow、Smart Capture、Codex renderer、MCP 与 Skill 调用链，核对结果已追加到 `D_执行权切换与API调用链矩阵.md`。
3. 已找全 custom collection 的列表采集、续采和详情采集入口，并区分 Workflow/Smart Capture 的非 custom 执行路径。
4. 已固定 Workflow Run 历史 ID、Context 快照与 Context 类型下拉的当前 characterization；历史读取仍由 Workflow API 提供，当前页面仍以 Workflow Run 状态拼装 Smart Capture 视图。
5. 已建立 Prefetch、Reservation、Handoff、Manual Analysis、Context、BossCapture 的现有测试清单与源码入口清单。
6. 已核对 parent/child relation、状态字段、completion event 幂等与 Smart Capture snapshot/polling：当前源码尚未形成 E 契约要求的完整持久化闭环，相关缺口记录在“已知 bug characterization”和 Handoff。
7. 未执行 Task01 及后续任务；没有提前迁移 owner、重构页面或切换 production execution authority。

### B. 改动文件

| 文件 | 改动 |
|---|---|
| `解决方案执行/00_Luna_修改前回归基线与功能迁移台账.md` | 补齐 Task00 完成记录、测试结果、源码入口、characterization、风险和 Task01 注意事项。 |
| `解决方案执行/A_现有功能迁移台账.md` | 追加 24 项能力的逐项源码入口与当前基线结论。 |
| `解决方案执行/D_执行权切换与API调用链矩阵.md` | 追加当前执行权/API/Codex 调用链的逐项源码核对。 |
| `解决方案执行/HANDOFF_Task00_回归基线与迁移台账.md` | 新增 Task01 可直接使用的回归基线与迁移交接记录。 |

### C. 测试

#### 修改前基线

后端命令：

```text
pytest -q backend/tests/api/test_fine_job_workflow_runs_api.py backend/tests/api/test_fine_job_boss_capture_api.py backend/tests/api/test_fine_job_workflow_api.py backend/tests/services/test_boss_capture_tasks.py
```

结果：`53 passed, 2 failed, 10 errors`，耗时约 2 分 13 秒。

前端命令：

```text
pnpm --filter fine-job-desktop test:run -- src/renderer/services/workflowCodexHandoff.test.ts src/renderer/services/fineJobWorkflowCodexController.test.ts src/renderer/stores/fineJobBossCapture.test.ts src/renderer/pages/fine-job/TaskCockpit.test.ts src/renderer/pages/fine-job/CodexWorkspace.test.ts
```

结果：未进入测试收集；Vitest 加载 `apps/desktop/vitest.config.ts` 时因当前环境 `spawn EPERM` 退出。

#### 修改后结果

文档修改后已重新执行同一组后端与前端命令。结果与修改前一致：后端 `53 passed, 2 failed, 10 errors`；前端仍因 `spawn EPERM` 未进入测试收集。文档没有改变可执行代码，因此没有引入新的测试行为差异。

#### 修改前失败清单

1. `backend/tests/api/test_fine_job_workflow_runs_api.py::test_create_workflow_run_exposes_real_search_context_snapshot`：断言的 `analysis_policy` 缺少实际返回的 `enabled: true` 字段。
2. `backend/tests/api/test_fine_job_workflow_runs_api.py::test_pause_preserves_running_capture_and_resume_allows_auto_advance`：测试期望 resume 后为 `running`，实际返回 `pending`。
3. `backend/tests/services/test_boss_capture_tasks.py` 下列 10 项在 `tmp_path` fixture 初始化时因 `C:\Users\su\AppData\Local\Temp\pytest-of-su` 访问被拒绝而报错：
   - `test_auto_detail_task_reports_completed_job`
   - `test_task_marks_job_seen_in_an_earlier_capture`
   - `test_filter_result_is_written_to_existing_history_job`
   - `test_repeated_review_job_without_detail_or_evaluation_is_reprocessable`
   - `test_repeated_job_with_detail_and_evaluation_is_duplicate`
   - `test_force_recaptures_a_completed_job_detail`
   - `test_delivery_evaluation_is_written_to_job_detail_snapshot`
   - `test_history_detail_task_updates_detail_without_incrementing_count`
   - `test_manual_detail_can_use_job_removed_from_automatic_gate`
   - `test_continue_capture_appends_to_same_task_and_history`
4. 前端 5 个指定测试文件未收集，统一阻断原因为 Vitest/esbuild 配置加载阶段 `Error: spawn EPERM`。

### D. 保护性回归基线

| 能力 | 当前已确认保留的入口/测试 | 当前状态 |
|---|---|---|
| Prefetch | `test_prefetch_stays_outside_current_run_state_and_reuses_completed_jd`；`test_completion_contract_abandons_ready_prefetch_before_new_analysis_batch`；`test_wait_for_user_keeps_ready_buffer_until_user_continue`；`test_partial_ready_failed_prefetch_promotes_smaller_batch_without_stalling` | Workflow telemetry/advance 中已有 ready buffer、部分失败 promotion 与 abandon 行为，尚未迁移 owner。 |
| Reservation | `test_prefetch_reservation_is_atomic_and_competing_batch_cannot_select_candidate`；`test_cancelled_prefetch_releases_reservation_for_later_selection` | 现有活动 reservation 原子占用与释放行为保留在 Workflow/Pipeline 路径。 |
| Handoff | `test_analysis_batch_handoff_attempt_ack_is_idempotent_and_rejects_stale_attempts`；`test_next_analysis_batch_only_handoffs_new_pending_items`；`test_start_ack_timeout_full_retry_releases_old_attempt_before_replacing_it` | claim、prompt-written、ACK、release、next batch 和 full retry 已有后端测试；当前 identity 仍是 Workflow Run。 |
| Manual Analysis | `create_manual_analysis_batch`、`BossCapture.vue:createManualCodexBatch`、Workflow analysis batch API | 手工批次与保存反馈入口存在；仍依赖 Workflow Run，未迁移到 Smart Capture。 |
| Context | `test_create_workflow_run_exposes_real_search_context_snapshot`；`test_context_soft_budget_blocks_run_before_search_starts`；`test_codex_reads_backend_context_snapshot_via_registered_tool`；Context 下拉 `deep_job_search/candidate_analysis` | 搜索 Context、candidate analysis Shared Base、预算阻断和 MCP 读取存在；实际 owner 仍是 `fj_workflow_context_snapshots`。 |
| BossCapture | `BossCapture.vue` 的智能采集/自定义采集 tabs、Planner、Candidate/JD、筛选、详情、投递建议、Codex、Context、历史和停止/续采函数；`test_fine_job_boss_capture_api.py` | 页面能力区齐全；current 与 Workflow 镜像仍混合，旧页面未重构。 |

### E. Authority / Owner 状态

- Current owner：当前页面 Smart Capture 视图由 `workflowStore.currentRun` 和 Workflow API 拼装；后端存在 `fj_smart_capture_current`，但 `main.py` 尚未注册 Smart Capture router，当前页面尚未切换为其唯一业务 current。
- Pipeline data owner：当前仍以 Workflow Run/Pipeline 表为主，`fj_boss_capture_batches.smart_capture_id` 只形成半迁移关联；independent Pipeline owner 尚未完成。
- Production execution authority：当前为旧 Workflow `advance_deep_job_search` / `boss_capture_task_manager.start_capture` 路径；Task09 前没有切换到 Smart Capture Engine。
- 历史兼容路径：Workflow Run API、Workflow analysis/context/handoff API、Workflow MCP tools、`workflowCodexHandoff.ts`、`CodexWorkspace.vue` 和 `CodexTerminalPanel.vue` 均保留并继续以 `workflow_run_id` 运行。

### F. 已知 bug characterization / 偏差与遗留风险

1. Smart Capture router/service 已有半成品，但 `backend/app/main.py` 没有 import/include Smart Capture router，API client 已有 `/api/fine-job/smart-captures/...` 路径却尚未接入应用。
2. `smart_captures.ACTIVE_STATUSES` 为 `running/pausing/paused/waiting_next_batch/interrupted`，遗漏契约要求占用 current 的 `pending` 与 `waiting_for_user`；linked create 仍有“创建后等待批次启动才接管 current”的延迟语义。
3. `recover_interrupted_smart_captures()` 会依据最近活动或最近更新时间选择 current，并且没有在 `main.py` startup 流程调用；与持久化 current identity 和 restart recovery 契约存在偏差。
4. `fj_smart_captures` 当前缺少 `waiting_reason`、`control_cause`、`state_version`、capabilities 与完整 result summary；数据库 CHECK 也未覆盖 `waiting_for_user`。
5. 当前没有 `fj_workflow_children`、持久化 child outcome event、`child_relation_id/event_id` 幂等键与 `child_state_version` 单调 guard 的实现证据。
6. Smart Capture snapshot 当前只返回 status/stage/message/batch/jobs 等字段，没有 E 契约要求的完整 capabilities/state_version 版本语义，也没有独立 polling/SSE 接线。
7. linked `resume_smart_capture()` 仍可能执行 Workflow resume 后再调用 `advance_deep_job_search()`；linked stop 仍可能直接调用 Workflow cancel。该行为只作为 Task03/09 前 characterization，不在 Task00 修复。
8. `BossCapture.vue` 当前通过 `restoreLatest(false)` 恢复 Workflow，并以 `workflowStore.currentRun` 临时构造 `currentSmartCapture`；历史 Run 输入与当前镜像隔离仍不完整。
9. Workflow analysis/context/manual batch、MCP deep_job_search Skill、renderer handoff/controller、CodexWorkspace/Terminal 仍把 `workflow_run_id` 作为主要 identity；independent Smart Capture 尚不能完整运行这些能力。
10. 自定义采集互斥分别存在于 BossCapture router 的 `assert_collection_start_allowed`、Smart Capture router 的 custom task 检查和 Codex tool 的同名 guard，尚未形成统一后端容量规则。

### G. 下一窗口 Handoff

Task01 只处理 Smart Capture 基础设施、current slot、Smart/Custom collection capacity 与 restart recovery。必须保留本文件及 `HANDOFF_Task00_回归基线与迁移台账.md` 的失败清单和入口清单，先为当前 characterization 建立回归保护，再修改 Task01 允许范围内的基础设施。

Task01 重点核对 `fj_smart_captures` 的 pending/current 占槽、`waiting_for_user` 兼容、startup recovery、Smart Capture router 注册和 custom capacity 的统一后端入口。不要迁移 Pipeline owner、不要创建 `fj_workflow_children`、不要实现 parent event、不要把 Workflow `advance` 切成 Smart Capture Engine，也不要重构 BossCapture 页面。
