# HANDOFF Task00：回归基线与迁移台账

执行日期：2026-09-17
分支：`task-cockpit-phase1`
执行前基线提交：`389c957ddad6a7af51f2d28d80f550285a688459`

## 1. 任务边界

Task00 只完成源码核验、现有测试基线和迁移保护记录。没有修改业务 schema、owner、API 行为、页面、执行 authority 或测试逻辑；Task01 及后续任务没有提前执行。

已更新：

- `00_Luna_修改前回归基线与功能迁移台账.md`
- `A_现有功能迁移台账.md`
- `D_执行权切换与API调用链矩阵.md`

## 2. 测试命令与结果

后端：

```text
pytest -q backend/tests/api/test_fine_job_workflow_runs_api.py backend/tests/api/test_fine_job_boss_capture_api.py backend/tests/api/test_fine_job_workflow_api.py backend/tests/services/test_boss_capture_tasks.py
```

修改前、修改后均为：`53 passed, 2 failed, 10 errors`。

前端：

```text
pnpm --filter fine-job-desktop test:run -- src/renderer/services/workflowCodexHandoff.test.ts src/renderer/services/fineJobWorkflowCodexController.test.ts src/renderer/stores/fineJobBossCapture.test.ts src/renderer/pages/fine-job/TaskCockpit.test.ts src/renderer/pages/fine-job/CodexWorkspace.test.ts
```

修改前、修改后均在 Vitest 配置加载阶段因 `Error: spawn EPERM` 退出，未进入测试收集。

## 3. 修改前失败清单

- `test_create_workflow_run_exposes_real_search_context_snapshot`：实际 `analysis_policy` 比测试期望多 `enabled: true`。
- `test_pause_preserves_running_capture_and_resume_allows_auto_advance`：resume 实际返回 `pending`，测试期望 `running`。
- `test_boss_capture_tasks.py` 的 10 项测试在 `tmp_path` 初始化时因 `C:\Users\su\AppData\Local\Temp\pytest-of-su` 访问被拒绝而报错；失败测试全名见 Task00 主台账。
- 前端指定的 handoff/controller/BossCapture/TaskCockpit/CodexWorkspace 5 个测试文件均因 `spawn EPERM` 未收集。

## 4. 源码入口清单

### Custom collection

1. `POST /api/fine-job/boss-capture/capture` → `boss_capture.py:183-215` → `start_capture(capture_source="custom")`。
2. `finejob.start_job_capture` → `fine_job_server.py:353-361` → `CodexToolService.start_job_capture:854-902` → custom `start_capture`。
3. `POST /api/fine-job/boss-capture/tasks/{task_id}/continue` → `continue_capture`。
4. `finejob.continue_job_capture` → `CodexToolService.continue_job_capture:904-917` → `continue_capture`。
5. `POST /api/fine-job/boss-capture/tasks/{task_id}/details` → `start_details`。
6. `finejob.collect_job_details` → `start_details`。
7. `POST /api/fine-job/boss-capture/history/{history_job_id}/details` → `start_history_detail`。
8. `finejob.collect_job_detail` → `start_history_detail`。

对照路径：`workflow_runs.py:309-325` 与 `smart_captures.py:564-600` 也调用 `start_capture`，但传入 `capture_source="smart"`；Task01 的容量互斥回归必须覆盖 custom、Workflow 和 Smart 三类路径。

### Workflow/Codex/Context

- Workflow Analysis API：`routers/fine_job/workflow_runs.py:122-246`、`services/fine_job/workflow_runs.py:466-960`。
- Handoff：claim、prompt-written、ACK、release、retry/resubmit 在 `workflow_runs.py:761-960`、`workflowCodexHandoff.ts`、`api.ts:1585-1620`。
- MCP Workflow tools：`fine_job_server.py:159-214`；deep search Skill：`.agents/skills/finejob/SKILL.md:85-106`。
- Context：`workflow_runs.py` 的 `fj_workflow_context_snapshots` 读写、`BossCapture.vue:989-1000`、`api.ts:1431-1434`。
- Workspace/Terminal：`CodexWorkspace.vue:225-268`、`CodexTerminalPanel.vue:54-59` 通过 `workflow_run_id` 定位。

## 5. 已固定的保护性回归能力

- Prefetch：ready buffer、Prefetch N+1、部分失败 promotion、等待用户保留 ready buffer、完成时 abandon。
- Reservation：活动岗位原子占用、竞争批次不能重复选择、取消 prefetch 后释放。
- Handoff：claim race、prompt-written、started ACK 幂等、旧 attempt 拒绝、next batch、超时 full retry、release。
- Manual Analysis：创建 Workflow analysis batch、选中岗位送 Codex、反馈保存。
- Context：搜索快照、candidate_analysis Shared Base、软预算阻断、MCP 读取、Context 类型下拉。
- BossCapture：智能/自定义 tabs、Planner、Candidate、JD/详情、筛选、投递建议、Codex、Prefetch 展示、Context、历史、停止和续采。

对应测试主要集中在：

- `backend/tests/api/test_fine_job_workflow_runs_api.py:747-1199, 1236-1515`
- `backend/tests/api/test_fine_job_boss_capture_api.py`
- `backend/tests/services/test_boss_capture_tasks.py`
- `apps/desktop/src/renderer/services/workflowCodexHandoff.test.ts`
- `apps/desktop/src/renderer/services/fineJobWorkflowCodexController.test.ts`
- `apps/desktop/src/renderer/stores/fineJobBossCapture.test.ts`
- `apps/desktop/src/renderer/pages/fine-job/TaskCockpit.test.ts`
- `apps/desktop/src/renderer/pages/fine-job/CodexWorkspace.test.ts`

## 6. 已知 bug characterization

1. Smart Capture router/service 未在 `main.py` 注册；API client 路径存在但应用未接线。
2. `ACTIVE_STATUSES` 遗漏 `pending`、`waiting_for_user`；linked pending current 接管存在延迟。
3. restart recovery 按最近活动/更新时间重新选择 current，且 startup 未调用 recovery。
4. Smart Capture 缺少 E 契约要求的 `waiting_reason`、`control_cause`、`state_version`、capabilities、result summary 完整字段。
5. 未发现 `fj_workflow_children`、持久化 child outcome event、`child_relation_id/event_id` 幂等与 `child_state_version` guard。
6. linked resume 仍可能走 Workflow resume + advance；linked stop 仍可能走 Workflow cancel。
7. BossCapture 仍 `restoreLatest(false)`，`currentSmartCapture` 由 `workflowStore.currentRun` 临时拼装。
8. Workflow Analysis/Context/Handoff、MCP Skill、renderer controller、Workspace/Terminal 仍以 `workflow_run_id` 为主要 identity。
9. custom capacity 检查分散在 BossCapture、Smart Capture router 与 Workflow service。

## 7. 最危险回归点

- Task01 若遗漏 `pending/paused/waiting_for_user/interrupted` 的 current slot，占槽语义会退化。
- custom、Workflow、Smart 任一入口绕过统一 collection capacity，会出现双执行器或错误恢复。
- startup recovery 若以时间推断 current，会把历史终态重新当作当前任务。
- 不得以修复基线失败为名改变 Workflow/Smart owner、提前切 Engine 或删除旧 Pipeline 能力。

## 8. Task01 注意事项

Task01 只处理 Smart Capture 基础设施、current slot、Smart/Custom collection capacity 和 restart recovery。先为当前 characterization 建立保护，再在允许范围内修改基础设施。

Task01 不迁 Pipeline owner，不创建通用 child relation/event，不实现 parent pause/resume/cancel 状态机，不切换 production execution authority，不重构 BossCapture，不删除 Workflow 历史、Context、Handoff、Prefetch、Reservation 或 Manual Analysis 能力。

最终提交 hash 在 Task00 提交记录及任务回复中报告。
