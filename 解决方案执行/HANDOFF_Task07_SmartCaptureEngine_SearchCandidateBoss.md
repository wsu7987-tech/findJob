# HANDOFF_Task07：Smart Capture Engine / Search / Candidate / BOSS

## A. 完成结论

- Search Combination、BOSS Capture Batch、Candidate Discovery、Candidate Pool 统一由 `smart_capture_id` 作为业务 owner。
- linked 与 independent 使用同一 `smart_capture_engine` 处理 Search/Candidate 结果。
- independent 运行链不读取或创建 Workflow Run。
- BOSS batch 通过 `smart_capture_id` 归属 Smart Capture；搜索任务保存 combination、筛选策略、深度和 batch operation ref。
- Candidate Pool 从 Smart Capture owner 下的 discovery 去重读取。
- 同一 batch 的重复完成回调保持幂等；同一 batch 的低页续采可处理新增岗位。
- pending/current 占用、容量互斥、browser interruption、pause/resume、restart recovery 和失败不可重试语义继续保留。

## B. Engine 调用链

### independent

`start_independent_capture`
→ `_start_new_batch`
→ `prepare_search_execution(smart_capture_id)`
→ `boss_capture_task_manager.start_capture`
→ `bind_batch` / `bind_capture_task`
→ `smart_captures._on_capture_task_updated`
→ `process_completed_batch`
→ Smart Capture owner 下的 discovery、combination metrics、candidate pool 和 snapshot。

### linked

`workflow_runs.advance_deep_job_search`
→ `start_linked_capture_batch`
→ 同一 `_start_new_batch` 与 `prepare_search_execution`
→ BOSS batch 绑定 Smart Capture owner
→ `workflow_runs._record_batch`
→ 同一 `process_completed_batch`
→ Workflow 继续消费 Smart Capture 结果。

Task09 前 linked 的 live start/后续编排 authority 仍由旧 Workflow Engine 持有；共享 Engine 已接入并由单一 runtime guard 防止同一 child 双启动。

## C. 本轮核对与修复

1. 修复同一 BOSS batch ID 的续采幂等判断：以累计页数和岗位数识别新增结果，避免复用 batch ID 时跳过新岗位。
2. `_task_for_batch` 按最新任务创建时间读取，保证 linked 续采使用最新搜索任务。
3. 修复 linked planner decision 写入时的 owner 参数，统一按 `smart_capture_id` 更新组合记录。
4. 补充 independent 低页续采、linked Candidate Pool、planner owner 写入回归测试。
5. 新增本 HANDOFF 文件。

## D. 验收结果

聚焦回归：

```text
.venv\Scripts\python.exe -m pytest -q backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_workflow_runs_api.py::test_fresh_only_count_excludes_historical_discoveries_and_records_source backend/tests/services/test_pipeline_owner_seam.py
38 passed
```

Task07 完整回归：

```text
.venv\Scripts\python.exe -m pytest -q backend/tests/test_db.py backend/tests/services/test_pipeline_owner_seam.py backend/tests/api/test_fine_job_smart_captures_api.py backend/tests/api/test_fine_job_workflow_runs_api.py backend/tests/services/test_boss_capture_tasks.py backend/tests/services/test_adaptive_search_planner.py
115 passed in 205.87s
```

启动验证：

- 后端通过 `scripts\start-backend.ps1 -NoReload` 启动，`GET /api/health` 返回 HTTP 200、`status=ok`。
- `GET /api/fine-job/smart-captures/current` 可访问。
- 前端 Vite `http://127.0.0.1:5173/` 返回 HTTP 200，并包含 `<div id="app"></div>`。
- 验证结束后 8000、5173 端口均已释放。

## E. 修改文件

- `backend/app/services/fine_job/smart_capture_engine.py`
- `backend/app/services/fine_job/smart_captures.py`
- `backend/app/services/fine_job/workflow_runs.py`
- `backend/app/services/fine_job/boss_capture_tasks.py`
- `backend/tests/api/test_fine_job_smart_captures_api.py`
- `backend/tests/api/test_fine_job_workflow_runs_api.py`
- `backend/tests/services/test_pipeline_owner_seam.py`
- `解决方案执行/HANDOFF_Task07_SmartCaptureEngine_SearchCandidateBoss.md`

## F. Task09 前边界与遗留风险

- linked production Engine authority 尚未切换，Task09 仍需完成 Engine Cutover 和旧 live callback/advance 清理。
- 当前完成回调会由 Smart Capture snapshot 链路和旧 Workflow 编排链观察；两条链调用同一 Engine，并通过任务结果与候选 discovery 去重保持幂等。
- 完成回调中的 Engine 异常沿现有后台监听边界隔离；independent 实际运行遇到持久化异常时，后续任务需要补充告警与重试闭环。
- 本轮完成了低页数开发态服务验证；未进行完整 Electron 产品手测，也未进行真实 BOSS 浏览器操作验收。
- Smart Capture Domain API、Renderer、MCP、Codex transport 和正式 parent outcome 消费继续由后续任务接线。

## G. 下一窗口重点

- 以本 Engine 调用链为基础接入 Task08A Domain API 与 snapshot/analysis/context/handoff。
- Task09 验证 linked child 的唯一 live authority、completion/outcome 消费与旧入口禁用。
- 保持历史 Workflow 查询为显式只读兼容路径，继续禁止 Workflow ID 回退为 independent runtime owner。
