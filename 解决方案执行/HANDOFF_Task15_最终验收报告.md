# HANDOFF Task15：最终验收报告

## 验收结论

任务 15 的 Smart Capture / Workflow 集成回归通过；全仓前端测试与类型检查仍有下述失败，因此**全仓测试门禁尚未通过**。真实 BOSS/Codex 登录链路未进行端到端人工操作。前后端启动和基本 HTTP 检查通过。

## 修改文件

| 文件 | 修改 |
|---|---|
| `backend/app/services/fine_job/workflow_runs.py` | 旧采集回调仅发布历史 Workflow 快照，移除结束后自动创建 Workflow advance 线程及死分支。 |
| `backend/app/main.py` | 更新历史快照运行时注释。 |
| `apps/desktop/src/renderer/pages/fine-job/CodexWorkspace.test.ts` | 补齐旧 Workflow 测试替身的 `startPolling`；将 Clear 保护断言放到现有终端面板 owner。 |
| 本报告 | 留下测试矩阵、旧路径核对、最终契约摘要与遗留风险。 |

## 完整回归矩阵

| 场景 | 结果 | 证据与边界 |
|---|---|---|
| linked OFF：创建、current、候选目标、child/parent 完成 | PASS | Workflow/Smart Capture API 回归包含原子身份创建、Cutover completion/outcome、OFF 目标完成。真实 BOSS 手测未做。 |
| linked ON：Candidate→JD→Analysis/Codex/Prefetch | PASS | Cutover linked Engine 首批 Analysis、domain API、handoff 与 Prefetch/Reservation 回归通过。真实 Codex 手测未做。 |
| independent OFF/ON、无隐藏 Workflow/child relation | PASS | independent 创建、OFF 完成、ON 配置和 Smart Capture domain/Codex 回归通过。 |
| Smart/custom 容量及 pending/paused/waiting current | PASS | 非终态占槽、custom 阻止 Smart、Smart 阻止 custom、并发创建回归通过。 |
| parent/child pause/resume/cancel/stop/interrupted/failed | PASS | 原子控制、优先级、failed 不可 retry、skip/end、迟到事件回归通过。 |
| restart、current 稳定、orphan 处理 | PASS | 持久化 current、restart、orphan pending 回归通过；真实进程重启手测未做。 |
| 状态、relation、event 幂等、snapshot/polling | PASS | DB/API 状态与版本、relation 顺序和摘要、重复/旧版本事件、独立快照流回归通过。 |
| history/current/control/Codex 隔离 | PASS | BossCaptureCurrent、BossCaptureHistory、CodexWorkspace、AppShell 组件回归通过。 |
| Pipeline 并行、reservation、handoff ACK/retry | PASS | 旧 characterization 与 Smart Capture domain API 回归通过。 |
| 页面保护项 | PASS | TaskCockpitNew、旧历史页、BossCapture、Codex、Planner/Context、结果区相关组件断言通过；真实页面逐项手测未做。 |
| 前后端启动 | PASS | 后端 `/api/health`、`/api/fine-job/smart-captures/current` 返回 200；前端 `http://127.0.0.1:15173/` 返回 200 且根节点存在。临时服务已停止。 |
| 全仓前端测试 | FAIL | 60 文件中 55 通过、5 失败；249 项中 233 通过、16 失败。失败集中在 BossChat 页面/Store、chat policy、旧 review Store、ReviewQueue。 |
| 前端类型检查 | FAIL | `vue-tsc --noEmit` 有 7 项既存测试 fixture 类型错误：TaskCockpit 1、chat policy 2、Workflow Codex controller 3、handoff 1。 |

### 实际命令

- `python -m pytest -q`（项目 `.venv` 解释器）指定 5 个 Smart Capture/Workflow API 文件及 2 个 service 文件：**111 passed**。
- `pnpm --filter fine-job-desktop test:run -- --silent=true` 指定 11 个相关组件/store/service 文件：**63 passed**。
- `pnpm --filter fine-job-desktop test:run -- --silent=true` 全量：**233 passed, 16 failed**。
- `pnpm --filter fine-job-desktop exec vue-tsc --noEmit`：**7 项测试 fixture 错误**。

## 旧路径 grep / 清理结果

| 旧路径 | 结果 |
|---|---|
| BossCapture `restoreLatest(false)` 推断 current | 无命中；current 由后端 Smart Capture current pointer/详情提供。 |
| Workflow Run 拼 `currentSmartCapture` | 无命中；linked parent、current、历史查询分状态源。 |
| linked parent 与 inspected history 共 store/control | BossCapture 用独立 `linkedParentWorkflowRun` 和 `inspectedHistoricalRun`。 |
| Workflow Run live 直接启动 BOSS/JD/Analysis/Prefetch | `advance_deep_job_search` 等旧函数仍用于历史 characterization，但 Cutover Guard 在入口拒绝 post-cutover live 执行；父级新启动通过 Child Adapter。 |
| `_on_capture_task_updated` / `_advance_after_capture_finished` live advance | 自动线程和后者函数已移除；旧回调只发布历史快照，Smart 回调触发 guard。 |
| `resume_smart_capture` resume + Workflow advance | 当前函数走 child 与 Smart Capture Engine，没有 Workflow advance 调用。 |
| App controller 调 `advanceFineJobWorkflowRun` 推 Prefetch | App 无调用；旧 API/store/controller 导出仍留作历史兼容，Smart controller 以 Smart Capture current 为 identity。 |
| 新 Codex prompt/MCP 强制 workflow id | Smart Capture prompt、MCP 工具和交接使用 `smart_capture_id`；Workflow 工具保留历史路径。 |
| `workflow_run_id` fallback 决定新 owner/completion | Pipeline 新写入由 `PipelineRepository.for_smart_capture` 负责；Workflow ID 是 linked 导航/历史读取键。 |
| delivery OFF waiting analysis / Manual Analysis 回滚终态 | OFF completion 和 completed 后人工 Analysis 不回滚 parent 的 API 回归通过。 |
| independent 隐藏 Workflow Run/relation/event | independent 无隐藏 relation/outcome event 的 API 回归通过。 |
| Smart/custom 互斥、非终态占槽 | 统一容量检查与 current API 回归通过。 |
| failed/interrupted 自动推进、failed retry | parent waiting/决策与 failed terminal capability 回归通过。 |
| parent pause/cancel 回调竞态 | parent 优先级与迟到事件回归通过。 |
| child event 重复/乱序覆盖 parent | `(child_relation_id,event_id)` 幂等与 `child_state_version` guard 回归通过。 |
| relation 只在 JSON/内存 | `fj_workflow_children` 持久化 sequence/status/result summary/双版本。 |
| independent 依赖 Workflow SSE | Smart Capture current/id 快照及独立事件流存在。 |
| 状态与 DB CHECK/type/API 不一致 | `waiting_for_user`、failed/stopped、版本字段相关迁移与 API 回归通过。 |
| 完整 Execution Config 只在 Workflow | `fj_smart_captures.execution_config_json` 持有 linked/independent 配置。 |
| 通用“立即推进” | BossCapture 无入口；旧 API/store 方法仅作历史兼容。 |

## 最终 schema / API / owner / call chain

- **Schema**：`fj_smart_capture_current` 保存唯一 current pointer；`fj_smart_captures` 保存 lifecycle、完整 execution config 和 `state_version`；`fj_workflow_children` 保存通用 parent relation、sequence、result summary、relation `state_version` 和 `child_state_version`；`fj_workflow_child_events` 保存 linked outcome 并按 relation/event identity 幂等。Pipeline 新记录以 `smart_capture_id` 为 owner，旧 Workflow 行由只读 adapter 读取。
- **API**：`GET /api/fine-job/smart-captures/current` 与 `GET /api/fine-job/smart-captures/{id}` 提供独立快照；Smart Capture start/pause/resume/retry/stop 与分析/Codex API 使用 Smart Capture ID；Workflow API 提供 parent 控制及历史读取。
- **Owner**：current 唯一身份来自持久化 Smart Capture pointer；child lifecycle/config/execution 由 Smart Capture 负责；parent relation/control 由 Workflow Run + `fj_workflow_children` 负责；production execution authority 是 Smart Capture Engine。
- **Call chain**：parent orchestrator → `ChildAdapter.start(child_type, child_ref)` → `SmartCaptureService.start(id)` → Smart Capture Engine → Pipeline repository；linked child outcome → 持久化 child event → parent 幂等消费。independent 直接走 Smart Capture 创建/启动，不建父任务。
- **历史兼容**：保留 Workflow Run/Context ID 读取、旧 Workflow Codex 工具与显式只读 Pipeline adapter；Cutover Guard 阻断其作为 Smart Capture live Engine。

## 手测结果与已知剩余问题

已完成一次前后端启动及 HTTP 冒烟检查。任务书列出的 13 步真实 BOSS/Codex 手测仍需具备登录会话和外部执行器的人工环境；本次未对外部平台执行采集或交接。

全仓前端 16 项失败位于聊天与旧 Review 测试，具体为 BossChat 页面 5、BossChat Store 5、chat policy 2、旧 review Store 3、ReviewQueue 1。任务相关 11 文件 63 项全过；这些失败没有在本任务中修改产品语义。类型检查 7 项测试 fixture 错误仍在。故“完整矩阵全部通过”的最终门禁仍需上述项目修复并完成真实外部手测后复验。

## 后续交接

1. 当前生产 Smart Capture Engine 是唯一 live authority；保留 Cutover Guard。
2. 旧 Workflow capture 回调现只发布历史快照；不再创建 advance 线程。
3. `fj_smart_captures` 与 `fj_workflow_children` 的 owner 边界保持不变。
4. independent 不创建 Workflow Run、child relation 或 parent outcome event。
5. API 快照使用整数 `state_version`；linked event 需要 `child_state_version` guard。
6. 历史 Workflow/Context 读取与旧 Codex 工具继续保留。
7. 真实 BOSS/Codex 13 步手测及全仓前端残余失败是最终验收待办。
8. 不要把旧 Workflow advance API 重新接入当前 Smart Capture 页面或 App controller。
