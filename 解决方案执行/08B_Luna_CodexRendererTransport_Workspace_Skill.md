# 任务 08B：Codex Renderer Transport / Auto Controller / Workspace / Terminal / Skill

> v0.2.2.4 封版拆分：本任务必须同时阅读并遵守 `E_状态_子任务_事件与实时传输契约.md`；08A 已提供 Smart Capture Domain API。本任务只迁**桌面 renderer/自动交接/工作区定位与 Skill prompt identity**，仍不做 Task 09 的正式 Engine Cutover。

## 已确认决策 / 本任务主要完成事项

- renderer handoff 不再以 `FineJobWorkflowRun` / `workflow_run_id` 作为新任务唯一 identity；
- independent Smart Capture 必须能进入完整 Codex handoff；
- linked 可附 parent workflow id 仅用于导航/历史；
- App-level controller 不再通过 `getLatestWorkflowRun()+advanceWorkflowRun()` 驱动 Smart Capture Pipeline；
- Task 09 后禁止 live fallback 回旧 Workflow Engine。

## 必查文件/链路

至少核对并迁移：

- `workflowCodexHandoff.ts`；
- `fineJobWorkflowCodexController.ts`；
- `CodexWorkspace.vue`；
- `CodexTerminalPanel.vue`；
- `.agents/skills/finejob/SKILL.md`；
- renderer/API types 与 session attach；
- 任何仍从 latest Workflow Run 推导当前 Codex 任务的逻辑。

## Owner-neutral Handoff Snapshot

建议形成：

```text
smart_capture_id
analysis_batch_id
handoff_attempt_id
status/capabilities
codex_session_ref
execution_config
optional parent_workflow_run_id   # navigation/history only
```

禁止把 `workflow_run_id` 继续作为 claim/ACK/save 的必填业务 key。

## App-level 自动 Controller

- 当前分析 ACK/完成后通过 Smart Capture Domain service/API 推进对应 domain transition；
- 不再调用通用 Workflow `advance` 来推动 Prefetch；
- Task 09 前可保留 legacy adapter 供旧任务，但不得对同一 live child 双启动；
- Task 09 后找不到 Smart owner 时必须显式报错/legacy-only，不能静默回退 live Workflow Engine。

## Workspace / Terminal / Skill

- 当前 Smart Capture Codex 导航以 Smart Capture/Analysis identity 为准；
- linked parent id 只作可选导航；
- inspected historical Workflow Run 不得污染当前 Smart Capture Codex session；
- `SKILL.md` 的新 deep-search 流程不得要求 independent 提供不存在的 Workflow Run ID；
- 08A 新增的 Smart Capture MCP tools 应成为新 prompt 的标准调用路径。

## 自动测试（强制）

1. renderer handoff 不要求 `FineJobWorkflowRun`；
2. independent Smart Capture 可 attach session / claim / ACK / save；
3. linked 使用同一 transport；
4. App controller 不调用 Workflow `advance` 作为 Smart Capture live 推进；
5. history Workflow 查看与 current Smart Capture Codex session 隔离；
6. old Workflow legacy/history path 仍可读，不成为 new live fallback。

## 用户手测

本任务可做一次最小桌面 smoke：打开 independent Smart Capture 的 Analysis/Codex，确认能定位到 Smart Capture/Analysis，而不是要求 Workflow Run ID。完整并行 Pipeline 在 Task 09 后再测。

## 高级模型复核

建议与 Task 09 一起重点复核。
