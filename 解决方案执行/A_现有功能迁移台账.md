# A. BossCapture / Workflow / Smart Capture 现有功能迁移台账（v0.2.2.4）

> 默认原则：迁移/重新接线，不通过删功能降低难度。

| 能力 | 最终 owner / 归属 | v0.2.2.4 处理 | 可删除？ | 验收重点 |
|---|---|---|---|---|
| 智能采集配置表单 | Smart Capture | 抽共享组件，BossCapture/驾驶舱复用 | 否 | 字段/默认值/回显/校验不丢 |
| Search Planner / Combination | Smart Capture | 迁 owner/service | 否 | linked/independent 同 Engine |
| BOSS Capture Batch | Smart Capture | batch 归 smart_capture | 否 | parent 不直接执行 |
| Candidate Pool | Smart Capture | 保留 | 否 | current capture 一致 |
| JD Detail / Batch | Smart Capture | 迁 owner | 否 | Pipeline 不退化 |
| Analysis Batch | Smart Capture | 迁 owner/API | 否 | independent 无 workflow id 可用 |
| Manual Analysis Batch | Smart Capture 后处理能力 | 保留；OFF completed 后不回滚 lifecycle | 否 | parent 不重新等待 |
| Codex handoff/ACK/retry/resubmit/re-handoff | Smart Capture | 迁 API/renderer/MCP/Skill identity | 否 | independent 可完整运行 |
| Codex Workspace / Terminal | Smart Capture Analysis | 不再只靠 workflow route identity | 否 | current task 不被历史 Run 污染 |
| Prefetch Batch / Item | Smart Capture | 保留并行优化 | 否 | Analysis N 时准备 N+1 |
| Candidate Reservation | Smart Capture Pipeline | 迁 owner/unique | 否 | 同岗位不重复活动占用 |
| Context snapshot/budget | 当前 Smart Capture + 历史 Workflow read | 新 current context 归 Smart Capture；历史旧 Run 可查 | 否 | 两套 view 不污染 |
| Workflow Run ID 手填 | 历史/Context 工具 | 保留，只读 inspected run | 否 | B 不替换 linked A |
| Context 类型下拉 | 历史/Context 工具 | 保留 | 否 | 选项/查看能力不丢 |
| linked parent 状态 | 父镜像 | 由 currentSmartCapture.workflow_run_id 唯一确定 | linked 时否 | 不 latest fallback |
| Smart Capture pause/resume/stop | Smart Capture | 保留 | 否 | independent 不影响 parent |
| parent pause/resume/cancel | Workflow parent | 父镜像调用父业务接口 | 否 | 后端同步 child |
| 通用“立即推进”按钮 | 无 | 删除用户 UI | **是** | 合法卡点有语义化动作 |
| Workflow 状态详情 | 分拆 | 父概要/Smart内部/历史 inspected 分开 | 否 | 不再大杂烩 |
| `restoreLatest(false)` 作为 current | 无 | 移除该用途 | 是 | current API 权威 |
| `workflowStore.currentRun` 混 parent+history | 无 | 拆状态 | 是（混用） | history 不串 control |
| 自定义采集 | Custom capture | 保留 | 否 | 与非终态 Smart Capture 全入口互斥 |
| 旧 Workflow Analysis API | 历史/legacy compatibility | 新运行链不依赖；必要只读/adapter 保留 | 视兼容而定 | 不作为 independent runtime 依赖 |
| FineJob workflow MCP tools | 历史兼容 | 新增/迁 SmartCapture domain tools | 否（旧历史可留） | 新 Prompt 用 smart_capture_id |

## Task 00 源码清点重点

- BossCapture 所有功能区；
- custom capture 所有 start path；
- Workflow Analysis/Handoff APIs；
- renderer Codex handoff/controller；
- MCP server/CodexToolService/FineJob Skill；
- Prefetch/reservation/handoff tests；
- current Smart Capture 与 Workflow history API。

## Task00 源码核对备注（2026-09-17）

| 能力 | 当前源码入口/证据 | characterization 结论 |
|---|---|---|
| 智能采集配置表单 | `apps/desktop/src/renderer/pages/fine-job/BossCapture.vue:119-165, 1710+`；智能采集字段、投递目标、Recommend/Review、Codex、Context、stop policy 仍在页面内维护。 | 字段存在，但尚未抽成与驾驶舱共享的独立组件/模型。 |
| Search Planner / Combination | `backend/app/services/fine_job/workflow_runs.py` 的 search planner/combination 逻辑；`BossCapture.vue:212-249` 展示 `workflowStore.currentRun.telemetry.search_planner`。 | 当前 owner 与执行状态仍在 Workflow Run telemetry。 |
| BOSS Capture Batch | `backend/app/db.py` 的 `fj_boss_capture_batches`；`smart_captures.py:230-258` 可写 `smart_capture_id`。 | 已有半迁移关联，批次执行仍由 `boss_capture_task_manager` 驱动。 |
| Candidate Pool | `workflow_runs.py` 的 candidate pool、`BossCapture.vue:359-369` 的 `workflowDisplayJobs`。 | 当前岗位展示能合并 Workflow 结果与 capture 结果，尚未切 Smart Capture owner。 |
| JD Detail / Batch | `boss_capture.py:/tasks/{task_id}/details`；`boss_capture_tasks.py:start_details`；Workflow `/workflow-runs/{id}` 相关 JD 推进。 | 列表详情与历史详情能力保留，Pipeline 仍以 Workflow/旧 capture task 为主。 |
| Analysis Batch | `backend/app/routers/fine_job/workflow_runs.py:130-143`；`workflow_runs.create_manual_analysis_batch`。 | Manual/automatic analysis batch 仍是 Workflow API。 |
| Manual Analysis Batch | `BossCapture.vue:1109-1135` 的 `createManualCodexBatch`；`api.ts:1537-1550` 的 Workflow endpoint。 | 手工分析能力存在，尚未以 `smart_capture_id` 为主 identity。 |
| Codex handoff/ACK/retry/resubmit/re-handoff | `workflow_runs.py:761-960`；`workflowCodexHandoff.ts`；`fineJobWorkflowCodexController.ts`。 | claim、prompt-written、ACK、release、retry、resubmit 都保留，当前以 Workflow Run 为 identity。 |
| Codex Workspace / Terminal | `CodexWorkspace.vue:225-268` 读取 `route.query.workflow_run_id`；`CodexTerminalPanel.vue:54-59` 返回驾驶舱时携带 Workflow ID。 | 当前任务定位依赖 Workflow route query。 |
| Prefetch Batch / Item | `workflow_runs.py` 的 `_advance_prefetch`、`_get_prefetch_summary`；`fineJobWorkflowCodexController.ts:18-41` 在 ACK 后调用 Workflow advance。 | Analysis N + Prefetch N+1 现有行为有测试保护，owner 尚未迁移。 |
| Candidate Reservation | Workflow prefetch/reservation 逻辑与 `test_prefetch_reservation_is_atomic_and_competing_batch_cannot_select_candidate`。 | 原子占用、竞争隔离与释放测试存在，当前 unique/owner 仍挂 Workflow。 |
| Context snapshot/budget | `workflow_runs.py:466-474, 3049-3074, 3148-3179`；`BossCapture.vue:989-1000`；`api.ts:1431-1434`。 | Context 快照和预算阻断存在，表和 API 仍以 Workflow ID 为主。 |
| Workflow Run ID 手填 | `BossCapture.vue:145-147, 952-980` 的 `smartRunLookupId` 与 `restoreSmartWorkflowRun`；Workflow `GET /{workflow_run_id}`。 | 历史 Run 可按 ID 读取，但当前镜像逻辑会限制输入回到 mirror ID。 |
| Context 类型下拉 | `BossCapture.vue` Context inspector 的 `smartContextChannel` 及 `deep_job_search/candidate_analysis` 两个 option。 | 下拉和查看能力存在，读取仍使用 Workflow context API。 |
| linked parent 状态 | `BossCapture.vue:157-164, 185-188` 以 `workflowStore.currentRun` 生成 `currentSmartCapture`/`smartWorkflowRun`。 | 当前 parent 镜像不是持久化通用 child relation 的结果。 |
| Smart Capture pause/resume/stop | `smart_captures.py:263-427`；`routers/fine_job/smart_captures.py:44-60`；当前 router 未在 `main.py` 注册。 | service 与三类 command 存在，应用路由尚未接线，linked command 仍桥接 Workflow。 |
| parent pause/resume/cancel | `workflow_runs.py` pause/resume/cancel；`smart_captures.py:296-300, 362-380, 423-426`。 | parent 接口存在，child 同步通过旧 Workflow bridge 完成。 |
| 通用“立即推进”按钮 | `TaskCockpitNew.vue`、`BossCapture.vue` 中仍可见 `advance` 调用/推进语义；旧 `TaskCockpit.vue` 保持不修改。 | 当前任务不删除 UI；后续任务按总览处理。 |
| Workflow 状态详情 | `workflow_runs.py:get_workflow_run` 返回 status/completion/context/handoff/prefetch；`BossCapture.vue` 同页展示多类信息。 | 业务详情、父状态、历史工具仍混在 Workflow response/page。 |
| `restoreLatest(false)` 作为 current | `fineJobWorkflowRun.ts:121-135`；`BossCapture.vue:658-660`。 | 仍用于岗位采集页恢复 Workflow，和 current Smart Capture API 权威目标不一致。 |
| `workflowStore.currentRun` 混 parent+history | `fineJobWorkflowRun.ts:19, 68-78, 121-135`；`BossCapture.vue:185-188`。 | 单一 store 同时承载 current、parent mirror 与历史 inspected Run。 |
| 自定义采集 | BossCapture `POST /api/fine-job/boss-capture/capture`；`CodexToolService.start_job_capture`；续采和详情入口见 Task00 入口清单。 | 自定义能力完整保留，互斥检查分散在多个入口。 |
| 旧 Workflow Analysis API | `routers/fine_job/workflow_runs.py:122-246`；`api.ts:1431-1645`。 | 历史/当前 Workflow API 继续可读写，未建立 Smart Capture domain 等价入口。 |
| FineJob workflow MCP tools | `fine_job_server.py:159-214` 的 context/run/analysis/handoff 工具；`.agents/skills/finejob/SKILL.md:85-106` 的 deep_job_search 流程。 | 工具与 Skill 均要求 Workflow Run ID，independent Smart Capture 尚未覆盖。 |

### Custom collection 全部启动/续采入口

| 类型 | 入口 | 下游实现 |
|---|---|---|
| 自定义列表采集 | `POST /api/fine-job/boss-capture/capture` | `boss_capture.py:183-215` → `assert_collection_start_allowed(..., "custom")` → `boss_capture_task_manager.start_capture(capture_source="custom")`。 |
| MCP 自定义列表采集 | `finejob.start_job_capture` | `fine_job_server.py:353-361` → `CodexToolService.start_job_capture:854-902` → 同一 `start_capture(capture_source="custom")`。 |
| 自定义列表续采 | `POST /api/fine-job/boss-capture/tasks/{task_id}/continue` | `boss_capture.py:319-336` → `continue_capture`，沿用原 task/搜索页。 |
| MCP 自定义列表续采 | `finejob.continue_job_capture` | `fine_job_server.py:364-370` → `CodexToolService.continue_job_capture:904-917` → `continue_capture`。 |
| 自定义任务详情采集 | `POST /api/fine-job/boss-capture/tasks/{task_id}/details` | `boss_capture.py:347-360` → `start_details`；页面 `BossCapture.vue:1373-1388` 触发。 |
| MCP 选中岗位详情采集 | `finejob.collect_job_details` | `CodexToolService.collect_job_details:984-1016` → `start_details`。 |
| 历史岗位详情采集 | `POST /api/fine-job/boss-capture/history/{history_job_id}/details` | `boss_capture.py:277-310` → `start_history_detail`。 |
| MCP 单历史岗位详情采集 | `finejob.collect_job_detail` | `CodexToolService.collect_job_detail:1105-1133` → `start_history_detail`。 |
| 非 custom 对照路径 | Workflow `advance_deep_job_search` 与 Smart Capture `_start_new_batch` | `workflow_runs.py:309-325`、`smart_captures.py:564-600` 都调用 `start_capture`，但传入 `capture_source="smart"`；Task01 互斥回归必须覆盖它们。 |
