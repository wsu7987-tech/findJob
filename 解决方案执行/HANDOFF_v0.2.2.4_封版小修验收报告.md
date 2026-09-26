# NOT PASSED

当前仍未满足 v0.2.2.4 最终封版条件。

- Task12 终态恢复与 terminal realtime 小修已完成，专项回归通过。
- 历史 frontend full、`vue-tsc`、启动冒烟通过；本轮未重跑这些检查。
- 历史 backend full 为 `624 passed / 16 failed`，Gate D 未通过；本轮未重跑 full suite。
- 本轮 Boss scraper 与 MCP targeted validation 全部通过。
- 当前 HEAD 为 `429eb2c2192321a0cc677390186d1aba46a7cb91`。
- BOSS 会话为 `needs_login`、`ready=false`，真实 BOSS/Codex 13 步未执行，Gate H 未通过。

## 1. 基线

| 项目 | 结果 |
|---|---|
| 执行时间 | 2026-09-26（Asia/Tokyo） |
| branch | `task-cockpit-phase1` |
| 历史 before SHA | `31a5e4bdddfcda0c5626cdd82932324b48636086` |
| 历史代码与测试小修 SHA | `ea958c6a8f7b71bbfc9bfe08e43a5ab5458202df` |
| 当前 HEAD（本轮定点复核） | `429eb2c2192321a0cc677390186d1aba46a7cb91` |
| 历史代码与测试 commit | `ea958c6 fix: close v0.2.2.4 sealing gaps` |
| 报告归档 commit | `docs: record v0.2.2.4 sealing validation`（本文件所在提交） |
| Node / pnpm / Python | `v22.16.0` / `10.8.1` / `Python 3.13.12` |

`ea958c6a8f7b71bbfc9bfe08e43a5ab5458202df` 是历史封版小修提交，不再代表当前代码 HEAD。本轮定点复核确认当前 HEAD 为 `429eb2c2192321a0cc677390186d1aba46a7cb91`。

### 修改文件

核心实现与专项测试：

- `apps/desktop/src/renderer/pages/fine-job/TaskCockpitNew.vue`
- `apps/desktop/src/renderer/pages/fine-job/TaskCockpitNew.test.ts`
- `apps/desktop/src/renderer/stores/fineJobWorkflowRun.ts`
- `apps/desktop/src/renderer/stores/fineJobWorkflowRun.test.ts`
- `apps/desktop/src/renderer/pages/fine-job/BossCapture.vue`
- `apps/desktop/src/renderer/pages/fine-job/BossCaptureCurrent.test.ts`

全仓前端门禁的 fixture/mock 兼容修复：

- `apps/desktop/src/renderer/pages/fine-job/BossChat.test.ts`
- `apps/desktop/src/renderer/pages/fine-job/ReviewQueue.test.ts`
- `apps/desktop/src/renderer/pages/fine-job/TaskCockpit.test.ts`
- `apps/desktop/src/renderer/services/fineJobChatPolicy.test.ts`
- `apps/desktop/src/renderer/services/fineJobWorkflowCodexController.test.ts`
- `apps/desktop/src/renderer/services/workflowCodexHandoff.test.ts`
- `apps/desktop/src/renderer/stores/fineJobBossChat.test.ts`
- `apps/desktop/src/renderer/stores/fineJobWorkflow.test.ts`

未修改 backend、schema、Pipeline owner、Workflow identity 或页面结构。

## 2. Task12 修复

### 原问题

`TaskCockpitNew.vue` 使用 `restoreLatest(false, "task_cockpit")`，重新进入页面时无法恢复上一轮 terminal Run，导致父终态、child timeline 和 result summary 丢失。

### 修改方式

入口恢复改为 `restoreLatest(true, "task_cockpit")`。后端 latest API 原有 `created_from` 条件继续隔离来源，排序仍按 `updated_at DESC, created_at DESC`。非终态 Run 恢复后由 Store 建立 Workflow SSE；terminal Run 进入 Store 后保持订阅关闭。

### 硬契约满足情况

- terminal remount：父终态、child timeline 和 result summary 保留。
- terminal + orchestration：终态时新任务编排区可见，运行态控制隐藏。
- nonterminal：`running`、`paused`、`waiting_for_user`、两类 child decision、`waiting_child_interrupted` 均继续隐藏编排区。
- terminal → new run：Store 创建新 Run 后，`currentRun` 切换为新运行态 Run。
- source isolation：恢复调用固定携带 `created_from="task_cockpit"`，不会恢复 `boss_capture` 来源。

测试证据：`TaskCockpitNew.test.ts` 10 项、`fineJobWorkflowRun.test.ts` 9 项均通过；本轮局部组合共 34 项通过。

## 3. terminal realtime 修复

### Workflow terminal

`startPolling()` 现在先检查 `currentRun` 与 terminal status。无 Run 或 terminal Run 时调用统一停止逻辑并立即返回，确保：

```text
pollingActive = false
streamActive = false
eventSource = null
```

`running`、`waiting_for_user`、`paused` 仍建立 Workflow SSE。

### linked parent

BossCapture 恢复 linked parent snapshot 后，仅对非终态 parent 调用 `startPolling()`。Store 层同时提供 terminal no-op 双重保证。

### Smart Capture terminal detail

BossCapture 对 `completed`、`stopped`、`failed` terminal current 保留 snapshot 展示，同时关闭页面级 detail EventSource；初始化、current identity 切换和终态快照到达时均不会新建 `/smart-captures/{id}/events`。全局 current SSE 保留，active/waiting/interrupted 行为未改变。

测试证据：BossCapture current/history、Workflow Store、Codex controller 等专项 10 个文件共 `69 passed`；backend Smart Capture/Workflow 专项 7 个文件共 `114 passed`。

## 4. 测试矩阵

### 本轮 targeted validation（2026-09-26）

| 检查 | 结果 |
|---|---|
| `backend/tests/services/test_boss_scraper_service.py::test_capture_jobs_calls_embedded_engine_and_resets_request_budget` | `1 passed` |
| `backend/tests/services/test_boss_scraper_service.py` | `11 passed` |
| `backend/tests/api/test_fine_job_codex_api.py::test_mcp_server_registers_exact_core_tool_set` | `1 passed` |
| `backend/tests/api/test_fine_job_codex_api.py` | `9 passed` |

Boss scraper 失败判定为测试 double 签名过时：生产实现仍在 `_CAPTURE_LOCK` 内重置请求计数，并保留 `progress_callback` / `should_stop` 接口。本轮仅让 `fake_scrape_details` 接收并校验 `should_stop=None`。

MCP 精确核对结果：`CORE_TOOLS` 有而 server 无的工具为 `[]`，server 有而 `CORE_TOOLS` 无的工具为 `[]`；两者顺序一致且均无重复。11 个 Smart Capture 工具全部存在。本轮未修改 Smart Capture/MCP production code。

本轮未运行 backend 或 frontend full suite。以下 full suite、历史 targeted、类型检查和启动冒烟结果均为既有验收记录，本轮没有覆盖或改写其结果。

### 历史验证记录

| Gate | 实际命令 / 检查 | 结果 |
|---|---|---|
| frontend local | `pnpm --filter fine-job-desktop test:run -- TaskCockpitNew.test.ts fineJobWorkflowRun.test.ts BossCaptureCurrent.test.ts --silent=true` | `34 passed / 0 failed` |
| backend targeted | `python -m pytest -q` + 5 个 Smart Capture/Workflow API 文件 + 2 个 service 文件 | `114 passed / 0 failed` |
| frontend targeted | Task15 相关 10 个组件、Store、service 文件 | `69 passed / 0 failed` |
| backend full | `python -m pytest -q` | `624 passed / 16 failed`，耗时 `980.30s` |
| frontend full（修复前） | `pnpm --filter fine-job-desktop test:run -- --silent=true` | `250 passed / 16 failed` |
| frontend full（fixture/mock 修复后） | 同上 | `266 passed / 0 failed`，60 个文件全过 |
| vue-tsc（修复前） | `pnpm --filter fine-job-desktop exec vue-tsc --noEmit` | 7 项 fixture 类型错误 |
| vue-tsc（修复后） | 同上 | `0 errors` |
| startup smoke | 见下节 | PASS |

### backend full 失败清单

以下 16 项分布在聊天、Codex 工具集合、公司治理、导入解析、活动进展、Boss scraper 与 Web draft。它们超出本次 Smart Capture / Workflow 小修边界，未修改无关产品语义：

1. `test_chat_observe_generate_confirm_and_send`
2. `test_new_message_invalidates_old_draft_and_manual_send_takes_over`
3. `test_immediate_mode_debounces_continuous_messages`
4. `test_pause_cancels_queued_send_action`
5. `test_dispatch_timeout_becomes_unknown_and_is_not_reclaimed`
6. `test_local_transport_write_and_message_sync_do_not_confirm_platform`
7. `test_send_enabled_closed_after_claim_requeues_without_dispatch`
8. `test_resume_action_requires_cached_selection_and_keeps_selected_filename`
9. `test_mcp_server_registers_exact_core_tool_set`
10. `test_cooldown_rules_require_detail_and_evaluation_before_exclusion`
11. `test_pdf_markdown_and_text_items_complete_stub_delivery_chain`
12. `test_manual_message_generation_allows_human_takeover_session`
13. `test_progress_acceptance_resume_review_reply_unknown_reason_and_soft_rejection`
14. `test_capture_jobs_calls_embedded_engine_and_resets_request_budget`
15. `test_clean_resume_text_removes_common_pdf_artifacts`
16. `test_commit_web_draft_creates_url_pool_item`

### 启动冒烟

| 检查 | 结果 |
|---|---|
| `GET /api/health` | 200 |
| `GET /api/fine-job/smart-captures/current` | 200 |
| frontend root | 200，存在 `#app` mount 节点 |
| TaskCockpitNew | 无头 Chrome 打开 `#/fine-job/cockpit`，页面出现“任务驾驶舱” |
| BossCapture | 无头 Chrome 打开 `#/fine-job/capture`，页面出现“岗位采集” |
| 启动级错误 | 两页未捕获 `pageerror` |

## 5. 真实 BOSS / Codex 13 步

环境检查：`GET /api/fine-job/platform-sessions/boss` 返回 `status="needs_login"`、`ready=false`。没有可用于真实 BOSS 采集的已登录会话，故未创建真实任务，也没有可记录的 `smart_capture_id` / `workflow_run_id` / Codex handoff。所有步骤均按原 Task15 顺序记录为 BLOCKED；真实 BOSS/Codex E2E 仍需用户提供已登录 BOSS 会话后执行。

| # | 输入/动作 | smart_capture_id | workflow_run_id | 关键状态 / Codex | 预期 | 实际 | 结果 |
|---|---|---|---|---|---|---|---|
| 1 | linked OFF | 未产生 | 未产生 | 未产生 | OFF 完整完成 | BOSS 未登录，无法开始 | BLOCKED |
| 2 | linked ON | 未产生 | 未产生 | 未产生 | Analysis/Codex/Prefetch 完成 | BOSS 未登录，无法开始 | BLOCKED |
| 3 | independent OFF | 未产生 | 不适用 | 未产生 | 无隐藏 Workflow 且完成 | BOSS 未登录，无法开始 | BLOCKED |
| 4 | independent ON + Codex/Prefetch | 未产生 | 不适用 | 未产生 handoff | 完整交接并完成 | BOSS 未登录，无法开始 | BLOCKED |
| 5 | Smart paused 时尝试 custom | 未产生 | 视场景 | 未产生 | 容量互斥 | 无真实 Smart 任务 | BLOCKED |
| 6 | custom running 时尝试 Smart | 未产生 | 不适用 | 未产生 | 容量互斥 | 无真实 custom 任务 | BLOCKED |
| 7 | parent/child pause/resume | 未产生 | 未产生 | 未产生 | 父子控制正确 | 无真实 linked 任务 | BLOCKED |
| 8 | child stop → waiting → skip | 未产生 | 未产生 | 未产生 | 父等待并可 skip | 无真实 linked 任务 | BLOCKED |
| 9 | restart interrupted | 未产生 | 视场景 | 未产生 | 合法恢复且不重复 | 无真实任务可重启 | BLOCKED |
| 10 | historical Run/Context | 未产生 | 未产生 | 未产生 | 历史读取不污染 current | 无本轮真实 Run | BLOCKED |
| 11 | side nav/cockpit 同 current | 未产生 | 视场景 | 未产生 | current identity 一致 | 无本轮真实 current | BLOCKED |
| 12 | OFF completed 后人工 Analysis | 未产生 | 视场景 | 未产生 | 不回滚 child/parent | 无真实 completed 任务 | BLOCKED |
| 13 | 刷新、断线重连、创建响应丢失 | 未产生 | 视场景 | 未产生 | 不产生第二 current/relation | 无真实任务与登录链路 | BLOCKED |

## 6. 旧路径 grep

| 检查项 | 结果与语义判断 |
|---|---|
| `advanceFineJobWorkflowRun` | 仅命中 legacy API、Workflow Store 方法及测试；App-level Smart Capture Codex controller 未调用，生产 Smart Capture authority 未回滚。 |
| `restoreLatest(false` | renderer 无命中。 |
| `restoreLatest(true` | 仅 TaskCockpitNew 使用，并固定 `created_from="task_cockpit"`。 |
| `startPolling` | Workflow Store 名称继续兼容、内部为 SSE；BossCapture 仅对非终态 linked parent 启动。其他命中属于 custom capture、旧页面或独立模块。 |
| `setInterval` | Smart Capture Codex controller 测试仍断言不建立定时器；实际命中属于 legacy Workflow controller、custom capture、BossChat、登录、draft 等独立路径。 |
| `currentSmartCapture` | BossCapture 页面状态由 Smart Capture current API/current SSE 建立；没有从 Workflow latest 推断 current。 |
| `workflow_run_id fallback` | 唯一命中位于 Smart Capture Analysis snapshot 响应归一化，用于保留 linked parent/history link；不参与 Pipeline owner、新写入或 completion authority。 |

## 7. 最终结论

**NOT PASSED**

当前仍未满足 v0.2.2.4 最终封版条件。

- Gate A（Task12）：PASS
- Gate B（terminal realtime）：PASS
- Gate C（历史专项测试及本轮 targeted validation）：PASS
- Gate D（历史 backend full）：FAIL，16 failed
- Gate E（历史 frontend full）：PASS，266 passed
- Gate F（历史 vue-tsc）：PASS，0 errors
- Gate G（历史启动冒烟）：PASS
- Gate H（真实 13 步）：BLOCKED，BOSS `needs_login`

Smart Capture / Workflow 封版专项通过；全仓 release gate 仍未通过；v0.2.2.4 不得宣告最终封版。
