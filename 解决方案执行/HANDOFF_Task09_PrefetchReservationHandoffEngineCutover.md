# Task 09：Prefetch、Reservation、Handoff 并行与 Engine Cutover 交接

## 1. 验收结论

Task 09 的 Engine Cutover 与 ON 模式并行链已完成。linked 与 independent 均以 Smart Capture 为 production Pipeline owner；Analysis N 收到 Codex started ACK 后会立即启动 Prefetch N+1，ready Prefetch 在当前分析完成后原子 promotion，最小 child completion/outcome 也已打通。

## 2. Cutover 前置证据

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| linked/independent 共用 Smart Capture production Engine | PASS | `ChildAdapter.start`、`start_smart_capture`、`smart_capture_engine.prepare_search_execution` 共用；Smart Capture Domain 独立入口测试通过 |
| 父任务只通过 child 身份启动 | PASS | `POST /api/fine-job/workflow-runs/{workflow_run_id}/children/{child_relation_id}/start` → `start_linked_child` → `child_adapter.start` |
| 同一 child 单次启动 | PASS | Cutover 专项测试与 live-start guard 测试通过 |
| parent completion/outcome | PASS | completed relation event 幂等消费并保留 child `result_summary`，父任务进入 completed |
| 旧 callback/advance 不再启动 live Engine | PASS | POST-CUTOVER guard、legacy callback 观测、旧分析 ACK/result/manual batch 入口保护测试通过 |
| Reservation/Handoff 基础契约 | PASS | active `job_id` 全局唯一；handoff claim/ACK 幂等；promotion 同事务转移 reservation |
| 完整 ON Analysis N 与 Prefetch N+1 接续 | PASS | ACK 后在 Analysis 未完成时 Prefetch 已 ready/collecting；Analysis 先完成和 Prefetch 先完成两种顺序都能 promotion |
| pause/restart 内部单元收敛 | PASS | JD/Prefetch operation 纳入 pause/stop；restart 将丢失执行器的单元收敛为 pending/interrupted |

## 3. 当前 production 调用链

```text
TaskCockpitNew
  → workflowRunStore.create
  → api.startFineJobWorkflowChild
  → POST workflow-runs/{run}/children/{relation}/start
  → workflow_runs.start_linked_child
  → child_adapter.start
  → smart_captures.start_smart_capture
  → _start_new_batch
  → smart_capture_engine.prepare_search_execution
  → boss_capture_task_manager.start_capture
  → bind_batch
```

完成批次由 Smart Capture listener 消费：

```text
BOSS batch completed
  → smart_capture_engine.process_completed_batch
  → smart_capture_engine.advance_completed_batch
  → formal JD reservation/detail
  → Analysis Batch N

Codex started ACK(N)
  → Smart Capture Engine 创建 Prefetch N+1 reservation/detail
  → Analysis N 与 Prefetch N+1 并行
  → ready promotion 为 Analysis Batch N+1
  → Smart Capture terminal / linked child relation outcome
```

## 4. 被禁用或转为 history 的旧 live 路径

- `_on_capture_task_updated` 对 Smart Capture callback 只记录并阻断，不推进旧 Workflow。
- `_advance_after_capture_finished` 与 `advance_deep_job_search` 受 POST-CUTOVER guard 保护。
- Workflow analysis ACK、analysis result、manual analysis batch 入口受 POST-CUTOVER guard 保护。
- App-level Workflow Codex controller 不再调用旧 `advance` 推进 Prefetch。
- 旧 Workflow 查询与 legacy API 保留历史兼容；旧 store 的 `advance` 和旧 TaskCockpit 的“立即推进”保留兼容入口，调用后由 guard 返回 `WORKFLOW_LIVE_EXECUTION_DISABLED`，不会启动 live Engine。

## 5. 静态核对

- 前端 Codex controller 中未发现 `advanceFineJobWorkflowRun` 或 `/advance` 调用。
- 后端旧 `_advance_prefetch`、旧 analysis ACK/result/manual batch 调用点仍存在于历史 Workflow service，但公开 live 入口已加 guard；测试阶段显式切回 PRE-CUTOVER 验证既有行为。
- 新 child start 路由、ChildAdapter、Smart Capture start、live-start claim/release、legacy callback guard 和终态事件保护均已存在。
- `git diff --check` 未发现空白错误。

## 6. 测试与启动检查

- `python -m pytest backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_smart_capture_domain_api.py backend/tests/api/test_fine_job_workflow_runs_api.py backend/tests/services/test_pipeline_owner_seam.py`：**92 passed**。
- `python -m pytest backend/tests/api/test_fine_job_smart_capture_domain_api.py backend/tests/api/test_fine_job_workflow_runs_api.py::test_cutover_linked_child_uses_smart_capture_engine_for_first_analysis_batch backend/tests/services/test_pipeline_owner_seam.py -q`：**28 passed**。
- `pnpm exec vitest run src/renderer/services/fineJobWorkflowCodexController.test.ts src/renderer/stores/fineJobWorkflowRun.test.ts src/renderer/services/fineJobSmartCaptureCodexController.test.ts`：**6 passed**。
- 后端独立端口启动检查：`GET /api/health` 返回 **HTTP 200**。
- 前端 Vite 独立端口启动检查：`GET /` 返回 **HTTP 200**。
- 启动检查使用的测试进程已停止，端口已释放。

## 7. 本轮修复与修改范围

- 修复 `_pause_current_capture_batch` 被 child start 插入位置遮蔽的问题，恢复真实暂停调用。
- 创建 Workflow Run 后改为启动 pending child，补充前端 API 与回归测试。
- 终态 relation event 不再被同状态迟到事件覆盖 `result_summary`。
- 为旧 Workflow analysis ACK/result/manual batch 及 resume 分支补充 Cutover guard。
- 调整测试 fixture，使 PRE-CUTOVER characterization 与 POST-CUTOVER production guard 分层验证。
- 迁移正式 JD、Analysis、Prefetch、reservation 与 ready promotion 到 Smart Capture Engine。
- 详情完成事件直接接续 Pipeline，支持 Analysis-first 与 Prefetch-first 两种竞态顺序。
- 内部 JD/Prefetch operation 纳入 pause/stop/restart 收敛，避免幽灵 running。
- Prefetch 可观快照变化同步推进 Smart Capture `state_version`。

## 8. 轮询核对

本轮没有新增产品轮询设计。继续沿用 Task 08B 的 Smart Capture Codex controller 定时读取和既有 Workflow SSE；Task 09 的改动移除了旧 controller 通过 `advance` 驱动 Prefetch 的行为。`任务01_轮询风险核对.md` 无需新增记录。

## 9. 回滚与遗留风险

- 生产回滚应保留 Smart Capture 为 execution authority，不恢复旧 callback 或前端 `advance` 推进 live Pipeline。
- 如需验证迁移前行为，仅在测试或受控迁移环境显式配置 PRE-CUTOVER。
- 真实 BOSS/Codex 低目标 ON 任务的人工观察仍需在可用账号与浏览器环境执行；自动测试已覆盖两种竞态顺序。
- ON 目标未达且当前候选池已耗尽时，现保留 `waiting_next_batch` 系统等待语义；完整完成边界仍由 Task 10 按任务书封严。
- 旧历史页面仍显示“立即推进”兼容控制，调用会被 guard 拒绝；后续可在历史 UI 清理窗口移除该入口。
