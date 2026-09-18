# HANDOFF_Task06：Pipeline Repository / Query Owner 切换与历史兼容

## A. 核对结论

- 新业务 Pipeline owner 为 `smart_capture_id`。
- linked 场景中的 `workflow_run_id` 继续作为 parent/history link。
- independent 场景允许 `workflow_run_id=null`，不创建或依赖 Workflow ID。
- 历史 Workflow 查询继续通过显式只读路径读取。
- production Engine 仍保持旧 Workflow Pipeline authority，未提前执行 Task09 Cutover。

## B. Repository owner 清单

以下 Pipeline 表已纳入 Smart Capture owner-aware repository：

| 表 | 新业务 owner | Workflow 字段用途 |
| --- | --- | --- |
| `fj_workflow_tasks` | `smart_capture_id` | linked parent/history link |
| `fj_workflow_job_discoveries` | `smart_capture_id` | linked parent/history link |
| `fj_workflow_search_combinations` | `smart_capture_id` | linked parent/history link |
| `fj_workflow_context_snapshots` | `smart_capture_id` | linked history link |
| `fj_workflow_analysis_handoffs` | `smart_capture_id` | linked history link |
| `fj_workflow_prefetch_batches` | `smart_capture_id` | linked parent/history link |
| `fj_workflow_prefetch_items` | `smart_capture_id` | linked parent/history link |
| `fj_workflow_candidate_reservations` | `smart_capture_id` | linked parent/history link |
| `fj_workflow_evaluation_feedback` | `smart_capture_id` | linked history link |
| `fj_codex_sessions` | `smart_capture_id` | linked history link |

## C. Legacy read adapter 边界

- `LegacyPipelineReadAdapter` 通过 `history_only=True` 固定使用 `workflow_run_id` 查询。
- 历史 adapter 只允许读取；调用 `insert_owned` 或 `update_owned` 返回 `LEGACY_READ_ONLY`。
- 历史 Workflow ID 不会写入 Smart Capture owner，也不会成为新运行链的 owner。
- linked legacy row 仅在 Smart Capture owner 可以可靠确认时回填；无法确认的记录保留 Workflow-only 历史读取路径。

## D. CRUD 与回归测试

执行命令：

```text
.venv\Scripts\python.exe -m pytest -q backend/tests/test_db.py backend/tests/services/test_pipeline_owner_seam.py backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_workflow_runs_api.py
```

结果：`94 passed in 191.36s`。

Task06 专项测试：`19 passed in 20.91s`。

覆盖内容：

- independent `workflow_run_id=null` 的 owner repository 写入、读取、更新；
- independent task 与 analysis handoff CRUD；
- handoff 通用列表查询排序；
- history adapter 只读边界；
- linked Pipeline 查询与历史 Workflow API 兼容；
- Search、Discovery、JD、Analysis、Context、Handoff、Prefetch、Reservation、Manual Analysis 保护性回归；
- production live Engine 单 authority guard。

## E. Workflow fallback 静态搜索

- `pipeline_repository.py` 的 Workflow 查询只位于显式 `LegacyPipelineReadAdapter` scope，不存在 Smart Capture 缺失时的隐式旧函数回退。
- `workflow_runs.py` 的 Pipeline 表查询、状态更新、Prefetch/Reservation 释放和 completion 统计均按 `smart_capture_id` scope 执行；`_pipeline_scope_for_workflow` 仅在没有 Smart Capture 的历史 Workflow 视图中返回 `workflow_run_id` 只读 scope，与 `LegacyPipelineReadAdapter` 共同构成显式历史兼容路径。
- 静态搜索 `workflow_run_id = ?` 在 Pipeline 访问中只保留 owner 解析与受控 legacy 回填；其余两处属于 `fj_workflow_children` 父编排关系查询。
- 旧 Workflow Engine 仍是 Task09 前唯一 production execution authority，但它访问的 Pipeline 数据已使用 Smart Capture owner，不会因此提前切换 live Engine。

## F. 启动与轮询核对

- 后端启动脚本成功启动 Uvicorn；`GET /api/health` 返回 `{"status":"ok"}`。
- 前端 Vite 成功启动；首页返回 HTTP 200，并包含应用挂载节点。
- 项目通用 `manual-smoke-test.ps1` 首次摘要创建时立即断言 `completed`，因异步任务尚未完成而失败；随后同一摘要 Run 已完成。该时序问题不属于 Task06 owner 链路，本任务未扩大范围修改通用 smoke 脚本。
- Task06 暂存改动未新增 polling、SSE 或定时器设计。
- 现有自定义采集轮询及其风险记录继续以 `解决方案执行/任务01_轮询风险核对.md` 为准，本任务未修改该文档。

## G. 本轮修复

1. `fj_workflow_analysis_handoffs` 没有 `created_at` 和 `id` 字段，repository 通用列表读取原先使用默认排序会触发 SQLite 列不存在错误。现已按 `claimed_at, handoff_attempt_id` 使用真实字段排序，并补充回归断言。
2. linked Pipeline 的部分旧编排辅助查询曾直接以 `workflow_run_id` 过滤，现已统一为 Smart Capture owner scope；历史读取仍由只读 adapter 承担。
3. repository 现在拒绝将 Smart Capture owner 的新记录关联到其他 Workflow Run，避免 parent/history link 跨 owner 错配。

## H. 遗留风险

1. Smart Capture Domain API、Renderer、MCP 与 Engine Cutover 仍由后续任务处理。
2. `workflow_runs.py` 的旧 Workflow 编排辅助链在 Task09 前继续保留，后续 Cutover 必须继续验证单一 execution authority。
3. 通用 smoke 脚本的异步完成等待需要后续专项处理；本任务仅记录，不改变其范围。

## I. 待修复问题：通用 smoke 的异步完成断言

- 现象：`scripts/manual-smoke-test.ps1` 创建摘要 Run 后立即断言 `status=completed`；接口创建成功时 Run 可能仍处于 `pending/running`，脚本因此提前失败。
- 结果：脚本报告失败时，后端摘要任务仍会继续执行，并可能随后正常进入 `completed`。
- 影响：产生 smoke 假失败，降低启动冒烟结果的可信度；不影响 Task06 Pipeline owner 逻辑。
- 建议修复：复用现有 Run SSE 事件流等待终态事件，再读取 Run 快照确认 `completed`；增加明确超时和终态失败信息。
- 轮询边界：建议不新增产品轮询；采用现有 SSE 等待不需要修改 `解决方案执行/任务01_轮询风险核对.md`。
