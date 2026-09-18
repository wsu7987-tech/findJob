# HANDOFF_Task05：Pipeline Schema Migration / Smart Capture Owner 字段

执行日期：2026-09-19

## A. 已完成

1. 在 `Database._ensure_pipeline_owner_schema` 中完成 Pipeline 原地迁移：新增/重建 Smart Capture owner 字段，保留可空 `workflow_run_id` parent/history link。
2. 对 Workflow-only 的 `NOT NULL`、复合主键/唯一约束和缺失 Smart Capture FK 执行 SQLite 安全 table rebuild；索引使用 `IF NOT EXISTS`，重复初始化幂等。
3. linked 旧数据仅按唯一 `fj_smart_captures.workflow_run_id` 可靠回填；prefetch item 从 batch、Codex session 从 handoff 继续回填。
4. 无法确认 Smart Capture 的 legacy row 保留原 `workflow_run_id`、owner 为空，作为历史只读数据，不伪造 `smart_capture_id`。
5. independent 记录可在 `workflow_run_id=null` 下写入 discovery、domain task、JD/Analysis task、Context、Handoff、Prefetch、Reservation、Feedback 与 Codex session。

## B. 改动文件与逐表 owner / FK / index / unique

- `backend/app/db.py`：Task05 原地 schema rebuild、owner FK 校验、回填与索引迁移代码。
- `backend/tests/test_db.py`：空库、旧 schema、重跑、回填、不可回填、独立 owner 与 PRAGMA 验证。
- `解决方案执行/HANDOFF_Task05_PipelineSchemaMigration_SmartCaptureOwner字段.md`：本任务交接与逐表约束说明。

| 表 | Smart Capture owner | parent/history link 与 FK | index / unique 语义 |
|---|---|---|---|
| `fj_workflow_tasks` | `smart_capture_id` | `workflow_run_id` 可空；两列分别 FK 到 Smart Capture / Workflow | task `id` 主键；按 smart/run + status 查询 |
| `fj_workflow_job_discoveries` | `smart_capture_id` | `task_id` 指向可独立创建的 domain task；两 owner FK | `(smart_capture_id, task_id, job_id)` 与 `(workflow_run_id, task_id, job_id)` 分区唯一；保留 job 时间索引 |
| `fj_workflow_search_combinations` | `smart_capture_id` | Workflow 可空；组合父链仍自引用 | owner + `identity_json` 分区唯一；owner sequence/status 索引 |
| `fj_workflow_context_snapshots` | `smart_capture_id + channel` | Workflow 可空 | smart/channel 与 workflow/channel 分区唯一 |
| `fj_workflow_analysis_handoffs` | `smart_capture_id + analysis_batch_id` | Workflow 可空；handoff 不再以 nullable Workflow 复合主键承载独立记录 | smart/batch 与 workflow/batch 分区唯一 |
| `fj_workflow_prefetch_batches` | `smart_capture_id` | Workflow 可空 | smart/source 与 workflow/source 分区唯一；状态索引 |
| `fj_workflow_prefetch_items` | `smart_capture_id` | batch/job FK 保留，batch owner 可独立追溯 | `(prefetch_batch_id, job_id)` 唯一；smart/status 索引 |
| `fj_workflow_candidate_reservations` | `smart_capture_id` | Workflow 可空 | active `job_id` 全局唯一保持；owner/job 分区唯一；smart/status 索引 |
| `fj_workflow_evaluation_feedback` | `smart_capture_id` | `workflow_task_id` 可空 FK，支持 independent feedback | task 与 smart/task 查询索引 |
| `fj_codex_sessions` | `smart_capture_id` + analysis/handoff 引用 | Workflow 可空；两 owner FK 可置空保留历史 | 保留 `updated_at` 索引，新增 smart/workflow 查询索引 |
| `fj_boss_capture_batches` | 已有 `smart_capture_id` | 通过 Smart Capture 的 linked link 导航 | 原有 batch/job 关联保持；本任务不重复加 owner 列 |

`fj_job_evaluations` / `fj_review_items` 是按岗位保存的全局结果与投递审核 artifact；本任务通过 Smart Capture owner 的 task/feedback 关联它们，不把历史岗位 artifact 强行改造成一次 Smart Capture 记录。

## C. 测试

- `.venv\Scripts\python.exe -m pytest -q backend/tests/test_db.py`：`13 passed`。
- `.venv\Scripts\python.exe -m pytest -q backend/tests/test_db.py backend/tests/services/test_pipeline_owner_seam.py backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_workflow_runs_api.py`：`92 passed`，165.65 秒。
- `pnpm --filter fine-job-desktop test:run -- src/renderer/services/fineJobWorkflowCodexController.test.ts src/renderer/services/workflowCodexHandoff.test.ts src/renderer/stores/fineJobWorkflowRun.test.ts`：`3 files passed, 11 tests passed`。
- 覆盖空库新建、旧 schema upgrade、migration 重跑、linked 回填、不可回填 legacy 保留、`PRAGMA table_info` / `foreign_key_list` / `index_list` / `index_info`、independent `workflow_run_id=null` 实际写入及 active reservation 唯一性。
- 以上测试均有 pytest cache 权限 warning，不影响结果。
- `scripts/start-backend.ps1 -NoReload -Port 18000`：后端正常启动，`GET http://127.0.0.1:18000/api/health` 返回 `200`。
- `pnpm --filter fine-job-desktop dev`：watch build 与 Vite 输出 ready 后 wrapper 在当前自动化启动环境退出，5173 未保持监听；直接使用同一 `vite.config.ts` 启动 renderer 后，5173 返回 `200` 且 HTML 正常。

## D. 轮询与越界核对

- 本任务没有新增 polling、SSE 或定时器设计，`解决方案执行/任务01_轮询风险核对.md` 不修改。
- 未切 repository owner、未切 production Engine、未修改前端业务入口，未提前执行 Task06 及后续任务。

## E. Authority / Owner 状态

- `fj_smart_captures`：Smart Capture lifecycle/config/execution authority。
- Pipeline 表：新写入具备 `smart_capture_id` owner；`workflow_run_id` 只作 linked parent/history link。
- production execution authority：仍为 Task09 前的旧 Workflow Pipeline，Task05 未切换。
- 历史兼容：可回填 linked 使用 Smart Capture；不能回填的 legacy 保留 Workflow-only read path，不伪造 owner。

## F. 遗留风险

1. Task06 仍需把 repository/query 新写入切到 Smart Capture owner；本任务只完成 schema 与 migration。
2. 现有 legacy Workflow 查询仍存在，不能把本任务的 schema owner 变化误认为 live Engine 已 cutover。
3. 桌面 dev wrapper 在当前自动化启动环境会在输出 Vite ready 后退出；renderer 直接启动验证通过。该问题属于既有启动编排/环境表现，本任务未改动其代码。

## G. 下一窗口 Handoff

1. 继续复用 `smart_capture_id` 作为新 Pipeline 查询与写入 identity。
2. linked 的 `workflow_run_id` 只用于父关联和历史导航。
3. 无法回填的 row 保持 `smart_capture_id=NULL`，由 legacy read adapter 处理。
4. active reservation 仍对 `job_id` 全局唯一，不新增跨 Smart Capture 重复占用策略。
5. Task06 迁 repository/query 时需保持现有 Prefetch、Reservation、Handoff、Context 行为。
6. Task09 前仍不得让 Smart Capture 与旧 Workflow 对同一 live child 双跑。
7. Task09 仍需完成 child outcome/event 闭环后再切 execution authority。
