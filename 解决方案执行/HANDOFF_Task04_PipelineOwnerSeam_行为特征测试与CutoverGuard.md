# HANDOFF_Task04：Pipeline Owner Seam、行为特征测试与 Cutover Guard

执行日期：2026-09-19

## A. 已完成

1. 建立 `PipelineOwnerContext`：以 `smart_capture_id` 作为 Pipeline identity，`workflow_run_id` 仅作为 linked parent/history 关联；支持 `task_cockpit` linked、`boss_capture` independent 和只读 history context。
2. 建立 Smart Capture snapshot contract 校验：固定 `state_version`、`capabilities={start,pause,resume,retry,stop}` 和必需快照字段；拒绝空 `smart_capture_id`、Workflow-only identity 和第二版本字段 `revision`。本任务只固定 contract，没有提前实现 Task08A snapshot/polling Domain API。
3. 建立 `CutoverGuard`：Task09 前 linked production authority 固定为旧 Workflow Pipeline；同一 live child 的启动 authority 与启动占用通过 guard 校验，重复启动可检测；Cutover 后旧 Workflow live execution、旧 capture callback 和 resume+advance 操作会被拒绝。
4. 旧 Workflow capture callback 只对 `capture_source=smart` 或带 Smart Capture 关联的回调触发 Guard，custom capture 不被误判为旧 Smart Capture live callback。
5. 批次创建后绑定异常时先请求停止已创建的执行器，避免释放启动占用后对同一 child 重复启动。
6. 新增 characterization tests，覆盖 Search Combination/Candidate/JD/Analysis、Handoff claim/ACK/release、Analysis N + Prefetch N+1、Reservation 唯一性、Context snapshot、Manual Analysis、Workflow-only identity、resume+advance Guard、custom callback 隔离和真实 start seam 双启动检测。
7. 保留既有 Workflow API tests；没有迁移 DB owner/schema，没有复制 `workflow_runs.py`，没有切换 production execution authority，没有新增轮询设计。

## B. 改动文件

- `backend/app/services/fine_job/pipeline_owner.py`：owner context、history read context 和 snapshot contract 校验。
- `backend/app/services/fine_job/cutover_guard.py`：唯一 execution authority、旧 callback 和同一 child 双启动保护。
- `backend/app/services/fine_job/smart_captures.py`：接入 owner context/snapshot contract，并保护批次绑定失败后的执行占用。
- `backend/app/services/fine_job/workflow_runs.py`：旧 Smart Capture callback 经过 Guard，custom callback 保持可用。
- `backend/tests/services/test_pipeline_owner_seam.py`：新增 owner seam、characterization 和 Cutover Guard tests。
- `apps/desktop/src/renderer/services/fineJobWorkflowCodexController.ts`：App-level 旧 Workflow controller 的 authority guard。
- `apps/desktop/src/renderer/services/fineJobWorkflowCodexController.test.ts`：Cutover 后拒绝 latest Workflow fallback 的前端 test。
- `解决方案执行/HANDOFF_Task04_PipelineOwnerSeam_行为特征测试与CutoverGuard.md`：本任务交接记录。

## C. 测试

- 后端 Task04 回归：
  `.venv\Scripts\python.exe -m pytest -q backend/tests/services/test_pipeline_owner_seam.py backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_workflow_runs_api.py`
  结果：`79 passed`，148.16 秒；1 个 pytest 缓存目录权限 warning。
- 新增 seam 测试单独回归：
  `.venv\Scripts\python.exe -m pytest -q backend/tests/services/test_pipeline_owner_seam.py`
  结果：`17 passed`，17.10 秒；1 个 pytest 缓存目录权限 warning。
- 前端 Task04/Task03 相关回归：
  `pnpm --filter fine-job-desktop test:run -- src/renderer/services/fineJobWorkflowCodexController.test.ts src/renderer/services/workflowCodexHandoff.test.ts src/renderer/stores/fineJobWorkflowRun.test.ts`
  结果：`3 files passed, 11 tests passed`。首次沙箱运行因 esbuild `spawn EPERM` 未进入测试，提升权限后同一命令通过。
- 前后端启动测试：使用 `scripts/start-backend.ps1 -NoReload -Port 18000` 独立启动后端，Uvicorn 正常监听且 `/api/health` 返回 200；桌面端 watch build、Electron 和 Vite 均启动，Vite 因 5173 已占用切换至 5174；访问 `http://127.0.0.1:5174/` 与 8000 后端 `/api/health` 均返回 200。

## D. 保护性回归

- Search Combination 与 Candidate Pool：仍由既有 Workflow Pipeline 推进，新增测试确认组合 identity 和候选进入 JD/Analysis 的链路。
- JD Detail / Analysis Batch / Analysis Item：既有 API 测试继续通过，新增测试确认 JD task 与 analysis task 数量及状态。
- Codex Handoff：claim、release、重新 claim、ACK 的旧状态语义保持；ACK 后 Prefetch 仍并行准备下一批。
- Prefetch / Reservation：ready promotion 和活动 reservation 唯一性保持，未改为串行 Pipeline。
- Context：candidate analysis snapshot 仍可读取，Context budget 等原有 API characterization 继续通过。
- Manual Analysis：既有候选池冻结后仍可创建手工 Analysis batch，未改写 Pipeline 的历史结果语义。
- History / compatibility：Workflow Run、Workflow analysis/handoff API 和旧 controller 默认路径保留；history context 明确只读。

## E. Authority / Owner 状态

- 当前 owner seam：`PipelineOwnerContext.identity = smart_capture_id`；linked 的 `workflow_run_id` 只用于 parent/history 关联。
- Pipeline data owner：Task04 只建立 seam 和 characterization，现有 Workflow Pipeline 表仍保持原 owner；Task05/06 才执行 schema/repository owner 迁移。
- production execution authority：Task09 前仍是唯一旧 Workflow Pipeline Engine；linked Smart Capture 没有第二套 live Engine。
- 独立 Smart Capture：可使用 owner-neutral seam 做独立入口测试，不能与同一 live child 的 Workflow authority 双启动。
- 历史兼容路径：旧 Workflow Run/history API、Workflow handoff API、Workflow controller legacy adapter 保留；Cutover 后只能做 history/compatibility 读取或受控拒绝，不能回退启动旧 Engine。

## F. 偏差 / 未决风险

1. 没有新增轮询；`任务01_轮询风险核对.md` 未修改。现有前端 controller 的 `setInterval` 仍是既有 Workflow legacy 行为，Task04 只增加 authority guard。
2. `CutoverGuard` 的同一 child 启动占用是进程内 seam 保护；跨进程/重启后的持久化执行权仍由现有数据库 current/capacity 规则和后续 Task05～09 迁移补齐。
3. 启动环境已有端口占用：8000 已存在健康后端，5173 已占用因此 Vite 使用 5174；Chromium cache 权限和 DevTools Autofill 提示不影响页面/健康接口启动。
4. 本任务没有实现 Smart Capture Domain API、正式 snapshot/polling transport、Pipeline schema owner 迁移或 Task09 Cutover；这些保持在后续任务边界内。

## G. 下一窗口 Handoff

1. 当前 Task04 的 `PipelineOwnerContext` 字段为 `smart_capture_id`、可选 `workflow_run_id`、`source`、`config`、`contract`。
2. 新代码应优先从 Smart Capture identity 解析 owner，不能从 latest Workflow Run 猜 current。
3. Task09 前 linked live start 继续走旧 Workflow authority；不要让 Smart Capture service 对同一 linked child 启动第二个执行单元。
4. Task05 需要逐表检查 `smart_capture_id`、Workflow link、FK、复合主键、唯一索引和 independent insert/read，不能只添加字段。
5. Task06 需要复用本 seam 做 repository/query owner 切换，legacy rows 走只读兼容，不伪造 Smart Capture identity。
6. Task09 需要在正式切换前证明最小 completion/outcome 闭环、relation identity、event 幂等和 child version guard。
7. Task09 后旧 callback、旧 resume+advance、旧 App-level controller 只能被拒绝或委托 Smart Capture，不能重新启动旧 Workflow Engine。
